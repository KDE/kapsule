# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup step: mount custom directories specified at creation."""

import json
import os

from ...incus_client import IncusError
from ..constants import KAPSULE_CUSTOM_MOUNTS_KEY
from ..contexts import UserSetupContext
from . import user_setup_pipeline


@user_setup_pipeline.step(order=200)
async def mount_custom_dirs(ctx: UserSetupContext) -> None:
    """Mount custom directories specified at container creation.

    Reads the ``user.kapsule.custom-mounts`` config key (a JSON array
    of host paths) and adds each as an Incus disk device.
    """
    raw = ctx.instance_config.get(KAPSULE_CUSTOM_MOUNTS_KEY, "")
    if not raw:
        return

    try:
        custom_mounts: list[str] = json.loads(raw)
    except json.JSONDecodeError:
        ctx.warning(f"Invalid custom-mounts config: {raw}")
        return

    for mount_path in custom_mounts:
        # Sanitise the path for use as an Incus device name
        safe_name = mount_path.strip("/").replace("/", "-").replace(".", "-")
        device_name = f"kapsule-mount-{safe_name}"
        container_path = mount_path  # Same path inside container

        if not os.path.isdir(mount_path):
            ctx.warning(f"Custom mount source does not exist: {mount_path}")
            continue

        ctx.info(f"Custom mount: {mount_path} -> {container_path}")
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
            ctx.warning(f"Failed to mount {mount_path}: {e}")
