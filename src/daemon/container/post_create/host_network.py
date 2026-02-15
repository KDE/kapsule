# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Post-create step: mask services incompatible with host networking."""

from ...incus_client import IncusError
from ..contexts import PostCreateContext
from . import post_create_pipeline


@post_create_pipeline.step
async def host_network_fixups(ctx: PostCreateContext) -> None:
    """Mask services that don't work with host networking.

    Kapsule containers share the host's network namespace, so there are no
    network interfaces for systemd-networkd to manage inside the container.
    This causes systemd-networkd-wait-online.service to wait for a timeout
    (~30s) before services like Docker can start.

    We mask that service since the host network is already online.
    """
    ctx.info("Masking systemd-networkd-wait-online.service (host networking)")
    try:
        await ctx.incus.create_symlink(
            ctx.name,
            "/etc/systemd/system/systemd-networkd-wait-online.service",
            "/dev/null",
            uid=0,
            gid=0,
        )
    except IncusError as e:
        # Not fatal - some images may not have systemd
        ctx.warning(f"Could not mask systemd-networkd-wait-online: {e}")
