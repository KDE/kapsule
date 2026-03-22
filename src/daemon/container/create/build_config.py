# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Creation pipeline steps: validate, parse image, build config and devices."""

from __future__ import annotations

import json
import logging

import httpx

from ...container_options import OptionValidationError, parse_options
from ...incus_client import IncusError
from ...models_generated import ImagesPostSource, InstanceSource
from ...operations import OperationError
from ..config_helpers import (
    base_container_config,
    base_container_devices,
    store_option_metadata,
)
from ..constants import (
    NVIDIA_HOOK_PATH,
)
from ..contexts import CreateContext
from . import create_pipeline

log = logging.getLogger(__name__)

# Map common server aliases to URLs
SERVER_MAP = {
    "images": "https://images.linuxcontainers.org",
    "ubuntu": "https://cloud-images.ubuntu.com/releases",
}

_KAPSULE_PROJECT_ID = 24978
_KAPSULE_GITLAB_API = "https://invent.kde.org/api/v4"
_KAPSULE_S3_BASE = "https://storage.kde.org/ci-artifacts/kde-linux/kapsule/j"


def is_kapsule_server(url: str) -> bool:
    """Return ``True`` if *url* points to the kapsule S3 artifact store.

    Kapsule server URLs contain a per-build job ID suffix
    (e.g. ``.../j/4177006``), so two URLs from different builds will never
    be equal even though they reference the same logical image server.
    This helper lets callers match on the stable prefix instead.
    """
    return url.startswith(_KAPSULE_S3_BASE + "/")


async def resolve_server(alias: str) -> str:
    """Resolve a server alias to a URL.

    Static aliases are looked up in SERVER_MAP. The ``kapsule`` alias is
    resolved dynamically by querying the GitLab API for the latest
    successful build job and constructing the S3 URL for that job's
    artifacts.
    """
    if alias == "kapsule":
        return await _resolve_kapsule_server()
    url = SERVER_MAP.get(alias)
    if not url:
        raise OperationError(
            f"Unknown server alias: '{alias}'. "
            f"Known aliases: {', '.join([*SERVER_MAP, 'kapsule'])}"
        )
    return url


async def _resolve_kapsule_server() -> str:
    """Query GitLab for the latest successful kapsule image build job.

    Uses the pipelines API (public) followed by the pipeline-jobs API
    (also public), because the top-level ``/jobs`` endpoint on
    invent.kde.org requires authentication even for public projects.
    """
    pipelines_url = (
        f"{_KAPSULE_GITLAB_API}/projects/{_KAPSULE_PROJECT_ID}"
        f"/pipelines?status=success&ref=master&per_page=20"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(pipelines_url)
        resp.raise_for_status()
        pipelines = resp.json()

        # Walk pipelines newest-first, looking for an image-build job.
        for pipeline in pipelines:
            jobs_url = (
                f"{_KAPSULE_GITLAB_API}/projects/{_KAPSULE_PROJECT_ID}"
                f"/pipelines/{pipeline['id']}/jobs"
            )
            resp = await client.get(jobs_url)
            resp.raise_for_status()
            jobs = resp.json()

            for preferred in ("build-images+publish", "build-images"):
                for job in jobs:
                    if job["name"] == preferred and job["status"] == "success":
                        job_id = job["id"]
                        server_url = f"{_KAPSULE_S3_BASE}/{job_id}"
                        log.info(
                            "Resolved kapsule server to %s (pipeline %s)",
                            server_url,
                            pipeline["id"],
                        )
                        return server_url

    raise OperationError(
        "Could not find a successful kapsule image build. "
        "Check https://invent.kde.org/kde-linux/kapsule/-/pipelines"
    )


@create_pipeline.step(order=-500)
async def validate_not_exists(ctx: CreateContext) -> None:
    """Check that a container with this name doesn't already exist."""
    if await ctx.incus.instance_exists(ctx.name):
        raise OperationError(f"Container '{ctx.name}' already exists")


@create_pipeline.step(order=-400)
async def parse_image_source(ctx: CreateContext) -> None:
    """Parse the image string into an InstanceSource."""
    image = ctx.image

    if ":" in image:
        server_alias, image_alias = image.split(":", 1)

        # "local:" references images already present in the Incus local image
        # store (imported via `kapsule image import`).  No remote server or
        # simplestreams metadata is involved.
        if server_alias == "local":
            ctx.source = InstanceSource(
                type="image",
                alias=image_alias,
                allow_inconsistent=None,
                certificate=None,
                fingerprint=None,
                instance_only=None,
                live=None,
                mode=None,
                operation=None,
                project=None,
                properties=None,
                protocol=None,
                refresh=None,
                refresh_exclude_older=None,
                secret=None,
                secrets=None,
                server=None,
                source=None,
                **{"base-image": None},
            )
            return

        server_url = await resolve_server(server_alias)
    else:
        server_url = "https://images.linuxcontainers.org"
        image_alias = image

    ctx.source = InstanceSource(
        type="image",
        protocol="simplestreams",
        server=server_url,
        alias=image_alias,
        allow_inconsistent=None,
        certificate=None,
        fingerprint=None,
        instance_only=None,
        live=None,
        mode=None,
        operation=None,
        project=None,
        properties=None,
        refresh=None,
        refresh_exclude_older=None,
        secret=None,
        secrets=None,
        source=None,
        **{"base-image": None},
    )


@create_pipeline.step(order=-390)
async def ensure_image_cached(ctx: CreateContext) -> None:
    """Ensure the image is cached locally in the Incus image store.

    For remote (simplestreams) images, downloads via ``POST /1.0/images``
    and stores the fingerprint on the context.  For local images, resolves
    the alias to a fingerprint.

    This step must run after ``parse_image_source`` so that ``ctx.source``
    is populated.
    """
    assert ctx.source is not None

    if ctx.source.server and ctx.source.protocol:
        # Remote image — download into local store (no-ops if already cached)
        ctx.progress.info("Ensuring image is cached locally...")
        image_source = ImagesPostSource(
            type="image",
            mode="pull",
            server=ctx.source.server,
            protocol=ctx.source.protocol,
            alias=ctx.source.alias,
            certificate=None,
            fingerprint=None,
            image_type=None,
            name=None,
            project=None,
            secret=None,
            url=None,
        )
        try:
            from ...progress_tracker import wait_operation_with_progress

            cached_image, op_id = await ctx.incus.download_image(image_source)
            if cached_image:
                ctx.image_fingerprint = cached_image.fingerprint
            elif op_id:
                operation = await wait_operation_with_progress(
                    ctx.incus,
                    op_id,
                    ctx.progress,
                    description="Downloading image...",
                    timeout=600,
                )
                if operation.metadata and "fingerprint" in operation.metadata:
                    dl_fingerprint: str = operation.metadata["fingerprint"]
                    ctx.image_fingerprint = dl_fingerprint
                else:
                    raise IncusError("No fingerprint in image download result")
        except (IncusError, httpx.HTTPError) as e:
            log.warning("Failed to pre-cache image: %s", e)
            # Fall through — create_instance will still try using ctx.source
    else:
        # Local image — resolve alias to fingerprint
        alias = ctx.source.alias
        if alias:
            fingerprint = await ctx.incus.get_image_fingerprint_by_alias(alias)
            ctx.image_fingerprint = fingerprint


@create_pipeline.step(order=-380)
async def read_image_defaults(ctx: CreateContext) -> None:
    """Read default options from the cached image's properties.

    Looks for the default-options JSON string under two property keys:

    * ``kapsule.default_options`` — set by ``metadata.yaml`` inside the
      ``incus.tar.xz`` (used for locally imported images).
    * ``requirements.kapsule_default_options`` — set by the simplestreams
      ``requirements`` dict (used for remotely downloaded images, since
      Incus overwrites ``metadata.yaml`` properties with its own
      computed set on simplestreams import).

    Populates ``ctx.image_defaults`` with the parsed dict.  If neither
    property is found or the value is unparseable, ``image_defaults``
    stays empty.
    """
    if not ctx.image_fingerprint:
        return

    try:
        image = await ctx.incus.get_image(ctx.image_fingerprint)
        props = image.properties or {}
        # Try simplestreams key first (most common), then metadata.yaml key
        raw_defaults = props.get("requirements.kapsule_default_options") or props.get(
            "kapsule.default_options"
        )
        if raw_defaults:
            parsed = json.loads(raw_defaults)
            if isinstance(parsed, dict):
                ctx.image_defaults = parsed
                log.info(
                    "Image defaults for %s: %s",
                    ctx.image_fingerprint[:12],
                    ctx.image_defaults,
                )
    except (IncusError, json.JSONDecodeError, TypeError) as e:
        log.warning("Could not read image defaults: %s", e)


@create_pipeline.step(order=-370)
async def parse_create_options(ctx: CreateContext) -> None:
    """Merge image defaults with user options and parse into ContainerOptions.

    Precedence: schema defaults < image defaults < user-specified options.

    Raises ``OperationError`` if user-supplied options fail validation.
    """
    try:
        ctx.opts = parse_options(ctx.raw_options, image_defaults=ctx.image_defaults)
    except OptionValidationError as e:
        raise OperationError(str(e))


@create_pipeline.step(order=-300)
async def build_base_config(ctx: CreateContext) -> None:
    """Build base container config (security, networking, NVIDIA hook)."""
    assert ctx.opts is not None
    ctx.instance_config = base_container_config(
        nvidia_drivers=ctx.opts.gpu and ctx.opts.nvidia_drivers,
    )

    ctx.progress.info(f"Image: {ctx.image}")
    if not ctx.opts.gpu:
        ctx.progress.info("GPU passthrough: disabled")
    if ctx.opts.gpu and not ctx.opts.nvidia_drivers:
        ctx.progress.info("NVIDIA driver injection: disabled")

    if NVIDIA_HOOK_PATH in ctx.instance_config.get("raw.lxc", ""):
        ctx.progress.dim("NVIDIA userspace drivers will be injected on start")


@create_pipeline.step(order=-200)
async def store_options(ctx: CreateContext) -> None:
    """Store kapsule option values as ``user.kapsule.*`` config keys."""
    assert ctx.opts is not None
    store_option_metadata(ctx.instance_config, ctx.opts)


@create_pipeline.step(order=-100)
async def build_devices(ctx: CreateContext) -> None:
    """Build base Incus devices (root disk, GPU, hostfs)."""
    assert ctx.opts is not None
    ctx.devices = base_container_devices(
        host_rootfs=ctx.opts.host_rootfs,
        gpu=ctx.opts.gpu,
    )
