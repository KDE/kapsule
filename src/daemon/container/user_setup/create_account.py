# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup step: create user group and account in the container."""

import subprocess

from ..constants import KAPSULE_MOUNT_HOME_KEY
from ..contexts import UserSetupContext
from . import user_setup_pipeline


@user_setup_pipeline.step(order=150)
async def create_account(ctx: UserSetupContext) -> None:
    """Create user group and account in the container."""
    # Create group
    ctx.progress.info(f"Creating group '{ctx.username}' (gid={ctx.gid})")
    result = subprocess.run(
        [
            "incus",
            "exec",
            ctx.container_name,
            "--",
            "groupadd",
            "-o",
            "-g",
            str(ctx.gid),
            ctx.username,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "already exists" not in result.stderr:
        ctx.progress.warning(f"groupadd: {result.stderr.strip()}")

    # When home is bind-mounted from the host, skip home creation (-M)
    # so we don't clobber existing files.  When home is container-local,
    # use -m so useradd creates it and copies /etc/skel (images can ship
    # default dotfiles like kde-builder config this way).
    mount_home = ctx.instance_config.get(KAPSULE_MOUNT_HOME_KEY, "true") == "true"
    home_flag = "-M" if mount_home else "-m"

    # Create user
    ctx.progress.info(f"Creating user '{ctx.username}' (uid={ctx.uid})")
    result = subprocess.run(
        [
            "incus",
            "exec",
            ctx.container_name,
            "--",
            "useradd",
            "-o",  # Allow duplicate UID
            home_flag,
            "-u",
            str(ctx.uid),
            "-g",
            str(ctx.gid),
            "-d",
            ctx.container_home,
            "-s",
            "/bin/bash",
            ctx.username,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "already exists" not in result.stderr:
        ctx.progress.warning(f"useradd: {result.stderr.strip()}")
