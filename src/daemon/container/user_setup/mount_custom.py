# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup step: mount custom directories specified at creation."""

import json
import subprocess

from ...incus_client import IncusError
from ...operations import OperationError
from ..constants import KAPSULE_CUSTOM_MOUNTS_KEY
from ..contexts import UserSetupContext
from . import user_setup_pipeline


@user_setup_pipeline.step(order=200)
async def mount_custom_dirs(ctx: UserSetupContext) -> None:
    """Mount custom directories specified at container creation.

    Reads the ``user.kapsule.custom-mounts`` config key (a JSON array
    of host paths, possibly containing ``~/`` prefixes) and adds each
    as an Incus disk device.

    Paths starting with ``~/`` are expanded per-user against
    ``ctx.home_dir`` and auto-created on the host if missing.
    Absolute paths are passed through as-is to Incus.
    """
    raw = ctx.instance_config.get(KAPSULE_CUSTOM_MOUNTS_KEY, "")
    if not raw:
        return

    try:
        custom_mounts: list[str] = json.loads(raw)
    except json.JSONDecodeError:
        ctx.progress.warning(f"Invalid custom-mounts config: {raw}")
        return

    if custom_mounts:
        ctx.progress.info(f"Custom mounts: {', '.join(custom_mounts)}")

    for raw_path in custom_mounts:
        # Expand ~/... against the entering user's home directory.
        is_home_relative = raw_path.startswith("~/")
        if is_home_relative:
            mount_path = ctx.home_dir + raw_path[1:]
        else:
            mount_path = raw_path

        # Sanitise the expanded path for use as an Incus device name
        safe_name = mount_path.strip("/").replace("/", "-").replace(".", "-")
        device_name = f"kapsule-mount-{safe_name}"
        container_path = mount_path  # Same path inside container

        # For ~/... paths, ensure the directory exists on the host.
        # mkdir -p is idempotent and runs as the user so ownership is
        # correct for all created intermediate directories.
        if is_home_relative:
            result = subprocess.run(
                ["mkdir", "-p", mount_path],
                capture_output=True,
                text=True,
                user=ctx.uid,
                group=ctx.gid,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                detail = f": {stderr}" if stderr else ""
                ctx.progress.warning(
                    f"Could not create custom mount directory: {mount_path}{detail}"
                )
                continue

        ctx.progress.info(f"Custom mount: {mount_path} -> {container_path}")
        try:
            await ctx.incus.add_instance_device(
                ctx.container_name,
                device_name,
                {
                    "type": "disk",
                    "source": mount_path,
                    "path": container_path,
                },
            )
        except IncusError as e:
            raise OperationError(f"Failed to mount {mount_path}: {e}")
