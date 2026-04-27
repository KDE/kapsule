# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup step: configure passwordless sudo."""

import subprocess

from ...operations import incus_context
from ..contexts import UserSetupContext
from . import user_setup_pipeline


@user_setup_pipeline.step(order=500)
async def configure_sudo(ctx: UserSetupContext) -> None:
    """Configure passwordless sudo for the user."""
    ctx.progress.info(f"Configuring passwordless sudo for '{ctx.username}'")
    # Ensure /etc/sudoers.d/ exists (Alpine and other minimal images may lack it)
    subprocess.run(
        ["incus", "exec", ctx.container_name, "--", "mkdir", "-p", "/etc/sudoers.d"],
        capture_output=True,
    )
    sudoers_content = f"{ctx.username} ALL=(ALL) NOPASSWD:ALL\n"
    sudoers_file = f"/etc/sudoers.d/{ctx.username}"
    async with incus_context("configure sudo"):
        await ctx.incus.push_file(
            ctx.container_name,
            sudoers_file,
            sudoers_content,
            uid=0,
            gid=0,
            mode="0440",
        )
