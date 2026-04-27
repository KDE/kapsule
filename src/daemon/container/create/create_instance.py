# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Creation pipeline step: call the Incus API to create the container."""

from __future__ import annotations

from ...incus_client import IncusError
from ...models_generated import InstanceSource, InstancesPost
from ...operations import OperationError
from ..contexts import CreateContext
from . import create_pipeline


@create_pipeline.step(order=0)
async def create_instance(ctx: CreateContext) -> None:
    """Create the container via the Incus API.

    By this point ``ctx.instance_config``, ``ctx.devices``, and
    ``ctx.source`` have been populated by earlier pipeline steps.

    If the image was pre-cached (``ctx.image_fingerprint`` is set),
    the instance source references the local fingerprint so Incus
    does not re-download the image.
    """
    ctx.progress.info("Creating container...")

    # Use local fingerprint when the image is already cached, otherwise
    # fall back to the original remote source set by parse_image_source.
    if ctx.image_fingerprint:
        source = InstanceSource(
            type="image",
            fingerprint=ctx.image_fingerprint,
            alias=None,
            allow_inconsistent=None,
            certificate=None,
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
    else:
        source = ctx.source

    instance_config = InstancesPost(
        name=ctx.name,
        profiles=[],
        source=source,
        start=True,
        config=ctx.instance_config,
        devices=ctx.devices,
        architecture=None,
        description=None,
        ephemeral=None,
        instance_type=None,
        restore=None,
        stateful=None,
        type=None,
    )

    try:
        op = await ctx.incus.create_instance(instance_config, wait=True)
        if op.status != "Success":
            raise OperationError(f"Creation failed: {op.err or op.status}")
    except IncusError as e:
        raise OperationError(f"Failed to create container: {e}") from e
