# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Creation pipeline step: call the Incus API to create the container."""

from __future__ import annotations

from ...incus_client import IncusError
from ...models_generated import InstancesPost
from ...operations import OperationError
from ..contexts import CreateContext
from . import create_pipeline


@create_pipeline.step(order=0)
async def create_instance(ctx: CreateContext) -> None:
    """Create the container via the Incus API.

    By this point ``ctx.instance_config``, ``ctx.devices``, and
    ``ctx.source`` have been populated by earlier pipeline steps.
    """
    ctx.info("Downloading image and creating container...")

    instance_config = InstancesPost(
        name=ctx.name,
        profiles=[],
        source=ctx.source,
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
        raise OperationError(f"Failed to create container: {e}")
