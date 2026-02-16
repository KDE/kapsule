# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Creation pipeline steps: validate, parse image, build config and devices."""

from __future__ import annotations

from ...models_generated import InstanceSource
from ...operations import OperationError
from ..config_helpers import base_container_config, base_container_devices, store_option_metadata
from ..constants import (
    NVIDIA_HOOK_PATH,
)
from ..contexts import CreateContext
from . import create_pipeline

# Map common server aliases to URLs
_SERVER_MAP = {
    "images": "https://images.linuxcontainers.org",
    "ubuntu": "https://cloud-images.ubuntu.com/releases",
}


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
        server_url = _SERVER_MAP.get(server_alias)
        if not server_url:
            raise OperationError(f"Invalid image format: {image}")
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


@create_pipeline.step(order=-300)
async def build_base_config(ctx: CreateContext) -> None:
    """Build base container config (security, networking, NVIDIA hook)."""
    ctx.instance_config = base_container_config(
        nvidia_drivers=ctx.opts.gpu and ctx.opts.nvidia_drivers,
    )

    ctx.info(f"Image: {ctx.image}")
    if not ctx.opts.gpu:
        ctx.info("GPU passthrough: disabled")
    if ctx.opts.gpu and not ctx.opts.nvidia_drivers:
        ctx.info("NVIDIA driver injection: disabled")

    if NVIDIA_HOOK_PATH in ctx.instance_config.get("raw.lxc", ""):
        ctx.dim("NVIDIA userspace drivers will be injected on start")


@create_pipeline.step(order=-200)
async def store_options(ctx: CreateContext) -> None:
    """Store kapsule option values as ``user.kapsule.*`` config keys."""
    store_option_metadata(ctx.instance_config, ctx.opts)


@create_pipeline.step(order=-100)
async def build_devices(ctx: CreateContext) -> None:
    """Build base Incus devices (root disk, GPU, hostfs)."""
    ctx.devices = base_container_devices(
        host_rootfs=ctx.opts.host_rootfs,
        gpu=ctx.opts.gpu,
    )
