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

# Map common server aliases to URLs.
#
# `kapsule` points at the stable simplestreams bucket published by the
# build-images+publish CI job on every master build. The URL is stable:
# refresh on a cached kapsule image just re-checks this index, no
# GitLab-API job-ID lookup required.
#
# The `simplestreams/` segment is a workaround for sync-s3-folder.py's
# URL regex, which requires a non-empty prefix after the bucket name --
# we can't upload to the bucket root. The bucket itself is reserved for
# future use (other artifact layouts), so naming the simplestreams tree
# explicitly inside it leaves room for siblings.
SERVER_MAP = {
    "images": "https://images.linuxcontainers.org",
    "ubuntu": "https://cloud-images.ubuntu.com/releases",
    "kapsule": "https://storage.kde.org/kapsule-images/simplestreams",
}

# `kapsule-mr:` resolves a merge-request reference on invent.kde.org to
# the simplestreams URL where that MR's `build-images` CI job uploaded
# its artifacts. Lets contributors test in-flight image changes with a
# single command, no mkosi setup required.
#
# Format:
#   kapsule-mr:<iid>:<image>                       -- canonical project
#   kapsule-mr:<namespace>/<project>!<iid>:<image> -- fork (! is GitLab's
#                                                     own MR-ref syntax)
#
# Examples:
#   kapsule-mr:42:archlinux
#   kapsule-mr:alice/kapsule!7:archlinux
#
# Resolution makes three anonymous GitLab API calls (project lookup, MR
# lookup, jobs list). The project must be public; private projects would
# need an access token plumbed through the daemon, which we don't do.
KAPSULE_MR_PREFIX = "kapsule-mr:"
GITLAB_HOST = "https://invent.kde.org"
GITLAB_API = f"{GITLAB_HOST}/api/v4"
KAPSULE_MR_DEFAULT_PROJECT = "kde-linux/kapsule"
# The CI artifact bucket layout mirrors the GitLab project path so that
# fork pipelines and the canonical project don't collide. See
# `.gitlab-ci.yml` ``UPLOAD_REMOTE`` and ``images/upload-images.sh``.
KAPSULE_MR_ARTIFACT_BASE = "https://storage.kde.org/ci-artifacts"


def resolve_server(alias: str) -> str:
    """Resolve a server alias to a URL via SERVER_MAP."""
    url = SERVER_MAP.get(alias)
    if not url:
        raise OperationError(
            f"Unknown server alias: '{alias}'. "
            f"Known aliases: {', '.join(SERVER_MAP)}"
        )
    return url


def parse_kapsule_mr_image(image: str) -> tuple[str, str, int]:
    """Parse a ``kapsule-mr:...`` reference into (project_path, image_alias, iid).

    Accepts:

    * ``kapsule-mr:<iid>:<image>``                     -- canonical project
    * ``kapsule-mr:<ns>/<project>!<iid>:<image>``      -- fork

    Raises ``OperationError`` on malformed input. Does no network I/O;
    network resolution happens in :func:`resolve_kapsule_mr_server`.
    """
    assert image.startswith(KAPSULE_MR_PREFIX)
    rest = image[len(KAPSULE_MR_PREFIX) :]

    # ``rpartition`` on ``:`` so the image alias is everything after the
    # last colon. This is unambiguous because image aliases never
    # contain ``:`` (they may contain ``/``, e.g. ``alpine/edge``, which
    # is why we don't ``split(':')`` and take ``[-1]`` on the whole
    # form -- we want to allow ``:`` in the ref-part if anyone ever
    # invents one).
    ref_part, sep, image_alias = rest.rpartition(":")
    if not sep or not ref_part or not image_alias:
        raise OperationError(
            f"Invalid kapsule-mr reference '{image}'. "
            f"Expected 'kapsule-mr:<iid>:<image>' "
            f"(e.g. 'kapsule-mr:42:archlinux') or "
            f"'kapsule-mr:<namespace>/<project>!<iid>:<image>' "
            f"(e.g. 'kapsule-mr:alice/kapsule!7:archlinux')."
        )

    if "!" in ref_part:
        project_path, _, iid_str = ref_part.rpartition("!")
        if not project_path or not iid_str:
            raise OperationError(
                f"Invalid kapsule-mr reference '{image}': "
                f"fork form must be '<namespace>/<project>!<iid>'."
            )
    else:
        project_path = KAPSULE_MR_DEFAULT_PROJECT
        iid_str = ref_part

    if not iid_str.isdigit():
        raise OperationError(
            f"Invalid kapsule-mr reference '{image}': "
            f"MR iid '{iid_str}' must be a positive integer."
        )

    return project_path, image_alias, int(iid_str)


async def resolve_kapsule_mr_server(project_path: str, iid: int) -> str:
    """Look up the most recent successful ``build-images`` job for an MR.

    Returns the simplestreams server URL for its uploaded artifacts.

    Makes three anonymous GitLab API calls (project lookup, MR lookup,
    jobs list). Raises ``OperationError`` with a user-actionable message
    on any failure -- the resolver is the one place that knows enough
    context to suggest "click play on the pipeline" or "the build
    expired, re-run it".
    """
    import urllib.parse

    encoded_path = urllib.parse.quote(project_path, safe="")
    pipeline_url = f"{GITLAB_HOST}/{project_path}"
    timeout = httpx.Timeout(10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # 1. Project path -> numeric id. We could skip this for the
            # canonical project (hardcode 24978) but the extra call is
            # cheap and avoids special-casing.
            proj_resp = await client.get(f"{GITLAB_API}/projects/{encoded_path}")
            if proj_resp.status_code == 404:
                raise OperationError(
                    f"GitLab project '{project_path}' not found on invent.kde.org. "
                    f"Check the namespace/path is correct and the project is public."
                )
            proj_resp.raise_for_status()
            project_id = proj_resp.json()["id"]

            # 2. MR -> head_pipeline. ``head_pipeline`` is the latest
            # pipeline for the MR's source branch; that's the one we
            # want, not whatever was running when the MR was opened.
            mr_resp = await client.get(
                f"{GITLAB_API}/projects/{project_id}/merge_requests/{iid}"
            )
            if mr_resp.status_code == 404:
                raise OperationError(
                    f"Merge request !{iid} not found in {project_path}."
                )
            mr_resp.raise_for_status()
            mr = mr_resp.json()
            head_pipeline = mr.get("head_pipeline")
            if not head_pipeline:
                raise OperationError(
                    f"MR !{iid} in {project_path} has no pipeline yet. "
                    f"Push a commit to its source branch to trigger one."
                )
            pipeline_id = head_pipeline["id"]
            pipeline_url = head_pipeline.get("web_url") or pipeline_url

            # 3. Jobs in the pipeline -> the build-images one. We look
            # for status==success specifically; pending/manual/failed
            # all mean "no artifacts to fetch yet" and the user needs
            # to act in the GitLab UI.
            jobs_resp = await client.get(
                f"{GITLAB_API}/projects/{project_id}/pipelines/{pipeline_id}/jobs"
            )
            jobs_resp.raise_for_status()
            jobs = jobs_resp.json()
    except httpx.HTTPError as e:
        raise OperationError(
            f"Failed to query invent.kde.org for MR !{iid} in {project_path}: {e}"
        ) from e

    build_jobs = [j for j in jobs if j.get("name") == "build-images"]
    if not build_jobs:
        raise OperationError(
            f"MR !{iid} in {project_path} has no 'build-images' job. "
            f"Did its pipeline include image changes?"
        )

    success_jobs = [j for j in build_jobs if j.get("status") == "success"]
    if not success_jobs:
        statuses = ", ".join(sorted({j.get("status", "?") for j in build_jobs}))
        raise OperationError(
            f"No successful 'build-images' job for MR !{iid} in {project_path} "
            f"(status: {statuses}). On MR pipelines build-images is manual -- "
            f"click play at {pipeline_url}."
        )

    # If multiple successes (e.g. retries), the highest job id is the
    # most recent.
    job_id = max(j["id"] for j in success_jobs)
    return f"{KAPSULE_MR_ARTIFACT_BASE}/{project_path}/j/{job_id}"


@create_pipeline.step(order=-500)
async def validate_not_exists(ctx: CreateContext) -> None:
    """Check that a container with this name doesn't already exist."""
    if await ctx.incus.instance_exists(ctx.name):
        raise OperationError(f"Container '{ctx.name}' already exists")


@create_pipeline.step(order=-400)
async def parse_image_source(ctx: CreateContext) -> None:
    """Parse the image string into an InstanceSource."""
    image = ctx.image

    # ``kapsule-mr:`` has its own multi-segment grammar (and resolution
    # requires async HTTP), so it's handled before the generic
    # ``server:alias`` split.
    if image.startswith(KAPSULE_MR_PREFIX):
        project_path, image_alias, iid = parse_kapsule_mr_image(image)
        ctx.progress.info(
            f"Resolving MR !{iid} in {project_path} on invent.kde.org..."
        )
        server_url = await resolve_kapsule_mr_server(project_path, iid)
    elif ":" in image:
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

        server_url = resolve_server(server_alias)
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
        raise OperationError(str(e)) from e


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
