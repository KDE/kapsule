# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup step: create user group and account in the container."""

import subprocess

from ..contexts import UserSetupContext
from . import user_setup_pipeline


@user_setup_pipeline.step(order=400)
async def create_account(ctx: UserSetupContext) -> None:
    """Create user group and account in the container."""
    # Create group
    ctx.info(f"Creating group '{ctx.username}' (gid={ctx.gid})")
    result = subprocess.run(
        [
            "incus", "exec", ctx.container_name, "--",
            "groupadd", "-o", "-g", str(ctx.gid), ctx.username,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "already exists" not in result.stderr:
        ctx.warning(f"groupadd: {result.stderr.strip()}")

    # Create user
    ctx.info(f"Creating user '{ctx.username}' (uid={ctx.uid})")
    result = subprocess.run(
        [
            "incus", "exec", ctx.container_name, "--",
            "useradd",
            "-o",  # Allow duplicate UID
            "-M",  # Don't create home directory
            "-u", str(ctx.uid),
            "-g", str(ctx.gid),
            "-d", ctx.container_home,
            "-s", "/bin/bash",
            ctx.username,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "already exists" not in result.stderr:
        ctx.warning(f"useradd: {result.stderr.strip()}")
