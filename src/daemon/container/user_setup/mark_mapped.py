# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup step: mark the user as mapped in container config."""

from ...incus_client import IncusError
from ...operations import OperationError
from ..contexts import UserSetupContext
from . import user_setup_pipeline


@user_setup_pipeline.step(order=900)
async def mark_mapped(ctx: UserSetupContext) -> None:
    """Mark the user as mapped in container config."""
    user_mapped_key = f"user.kapsule.host-users.{ctx.uid}.mapped"
    try:
        await ctx.incus.patch_instance_config(
            ctx.container_name, {user_mapped_key: "true"},
        )
    except IncusError as e:
        raise OperationError(f"Failed to update container config: {e}")
