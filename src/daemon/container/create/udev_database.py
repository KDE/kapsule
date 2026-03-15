# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Creation pipeline step: share the host's udev database with the container."""

from ...incus_client import IncusError
from ..contexts import CreateContext
from . import create_pipeline

# systemd mount unit that bind-mounts the host's udev database into the
# container at /run/udev.  This makes device metadata (ID_CDROM, ID_INPUT_*,
# etc.) visible to tools like udevadm and applications (e.g. K3B) that
# depend on it.
#
# We cannot use lxc.mount.entry for /run/udev because systemd mounts /run
# as tmpfs at boot, covering any earlier bind-mount.  A mount unit runs
# after /run is set up, so the bind-mount persists.
_UDEV_MOUNT_UNIT = """\
[Unit]
Description=Bind mount host udev database
DefaultDependencies=no
After=local-fs.target
Before=systemd-udevd.service

[Mount]
What=/.kapsule/host/run/udev
Where=/run/udev
Type=none
Options=bind,ro

[Install]
WantedBy=sysinit.target
"""


@create_pipeline.step(order=110)
async def share_udev_database(ctx: CreateContext) -> None:
    """Install a mount unit to share the host's udev database.

    Only applicable when host_rootfs is enabled, since the mount unit
    sources from /.kapsule/host/run/udev.
    """
    if not ctx.opts.host_rootfs:
        return

    try:
        await ctx.incus.push_file(
            ctx.name,
            "/etc/systemd/system/run-udev.mount",
            _UDEV_MOUNT_UNIT,
        )
        await ctx.incus.create_symlink(
            ctx.name,
            "/etc/systemd/system/sysinit.target.wants/run-udev.mount",
            "/etc/systemd/system/run-udev.mount",
            uid=0,
            gid=0,
        )
    except IncusError as e:
        ctx.progress.warning(f"Could not install udev database mount: {e}")
