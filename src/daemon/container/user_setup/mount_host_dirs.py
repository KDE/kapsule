# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup step: mount host runtime and X11 dirs (minimal rootfs mode)."""

from ...incus_client import IncusError
from ...operations import OperationError
from ..constants import KAPSULE_HOST_ROOTFS_KEY
from ..contexts import UserSetupContext
from . import user_setup_pipeline


@user_setup_pipeline.step(order=300)
async def mount_minimal_host_dirs(ctx: UserSetupContext) -> None:
    """Mount host runtime and X11 dirs when full rootfs is disabled.

    When the container doesn't have the complete host filesystem mounted
    at ``/.kapsule/host``, we add targeted mounts for ``/run/user/<uid>``
    and ``/tmp/.X11-unix`` so socket symlinks work.
    """
    has_host_rootfs = ctx.instance_config.get(KAPSULE_HOST_ROOTFS_KEY) == "true"
    if has_host_rootfs:
        return

    ctx.info("Minimal host mounts (no full rootfs)")

    # Mount /run/user/<uid> at /.kapsule/host/run/user/<uid>
    hostrun_device = f"kapsule-hostrun-{ctx.uid}"
    try:
        await ctx.incus.add_instance_device(
            ctx.container_name,
            hostrun_device,
            {
                "type": "disk",
                "source": f"/run/user/{ctx.uid}",
                "path": f"/.kapsule/host/run/user/{ctx.uid}",
                "shift": "false",
                "recursive": "true",
                "propagation": "rslave",
            },
        )
    except IncusError as e:
        raise OperationError(f"Failed to mount host runtime dir: {e}")

    # Mount /tmp/.X11-unix at /.kapsule/host/tmp/.X11-unix for X11
    try:
        await ctx.incus.add_instance_device(
            ctx.container_name,
            "kapsule-x11",
            {
                "type": "disk",
                "source": "/tmp/.X11-unix",
                "path": "/.kapsule/host/tmp/.X11-unix",
                "shift": "false",
                "recursive": "true",
                "propagation": "rslave",
            },
        )
    except IncusError as e:
        raise OperationError(f"Failed to mount host X11 dir: {e}")
