# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup step: mount or create the user's home directory."""

from ...incus_client import IncusError
from ...operations import OperationError
from ..constants import KAPSULE_MOUNT_HOME_KEY
from ..contexts import UserSetupContext
from . import user_setup_pipeline


@user_setup_pipeline.step(order=100)
async def mount_home(ctx: UserSetupContext) -> None:
    """Mount the user's home directory or create a container-local home."""
    mount_home = ctx.instance_config.get(KAPSULE_MOUNT_HOME_KEY, "true") == "true"

    if mount_home:
        ctx.info(f"Mounting home directory: {ctx.home_dir} -> {ctx.container_home}")
        device_name = f"kapsule-home-{ctx.username}"
        try:
            await ctx.incus.add_instance_device(
                ctx.container_name,
                device_name,
                {
                    "type": "disk",
                    "source": ctx.home_dir,
                    "path": ctx.container_home,
                },
            )
        except IncusError as e:
            raise OperationError(f"Failed to mount home directory: {e}")
    else:
        ctx.info("Home directory mount: skipped (disabled)")
        # Ensure the home path exists inside the container
        try:
            await ctx.incus.mkdir(
                ctx.container_name, ctx.container_home,
                uid=ctx.uid, gid=ctx.gid, mode="0700",
            )
        except IncusError:
            pass  # May already exist
