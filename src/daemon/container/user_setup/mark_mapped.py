# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup step: mark the user as mapped in container config."""

from ...operations import incus_context
from ..contexts import UserSetupContext
from . import user_setup_pipeline


@user_setup_pipeline.step(order=900)
async def mark_mapped(ctx: UserSetupContext) -> None:
    """Mark the user as mapped in container config."""
    user_mapped_key = f"user.kapsule.host-users.{ctx.uid}.mapped"
    async with incus_context("update container config"):
        await ctx.incus.patch_instance_config(
            ctx.container_name,
            {user_mapped_key: "true"},
        )
