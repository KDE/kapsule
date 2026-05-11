# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup step: mount or create the user's home directory."""

from ...operations import incus_context
from ..constants import KAPSULE_MOUNT_HOME_KEY
from ..contexts import UserSetupContext
from . import user_setup_pipeline


@user_setup_pipeline.step(order=100)
async def mount_home(ctx: UserSetupContext) -> None:
    """Mount the user's home directory or create a container-local home."""
    mount_home = ctx.instance_config.get(KAPSULE_MOUNT_HOME_KEY, "true") == "true"

    if mount_home:
        ctx.progress.info(
            f"Mounting home directory: {ctx.home_dir} -> {ctx.container_home}"
        )
        device_name = f"kapsule-home-{ctx.username}"
        async with incus_context("mount home directory"):
            await ctx.incus.add_instance_device(
                ctx.container_name,
                device_name,
                {
                    "type": "disk",
                    "source": ctx.home_dir,
                    "path": ctx.container_home,
                },
            )
    else:
        ctx.progress.info("Home directory mount: skipped (disabled)")
        # Don't create the home directory here -- useradd -m (in the
        # create_account step) will create it AND copy /etc/skel.  If we
        # mkdir first, useradd sees the dir already exists and skips skel.
