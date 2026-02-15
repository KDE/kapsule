# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""User setup step: enable loginctl linger for session-mode containers."""

import subprocess

from ..constants import KAPSULE_SESSION_MODE_KEY
from ..contexts import UserSetupContext
from . import user_setup_pipeline


@user_setup_pipeline.step(order=600)
async def enable_linger(ctx: UserSetupContext) -> None:
    """Enable loginctl linger if session mode is active."""
    session_mode = ctx.instance_config.get(KAPSULE_SESSION_MODE_KEY) == "true"
    if not session_mode:
        return

    ctx.info(f"Enabling linger for '{ctx.username}' (session mode)")
    result = subprocess.run(
        [
            "incus", "exec", ctx.container_name, "--",
            "loginctl", "enable-linger", ctx.username,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        ctx.warning(f"loginctl enable-linger: {result.stderr.strip()}")
