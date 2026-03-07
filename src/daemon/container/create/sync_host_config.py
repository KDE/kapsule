# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Creation pipeline step: sync host timezone, locale, and DNS into the container."""

from __future__ import annotations

from ..contexts import CreateContext
from . import create_pipeline


@create_pipeline.step(order=150)
async def sync_host_config(ctx: CreateContext) -> None:
    """Sync host timezone, locale, and DNS configuration into the new container."""
    ctx.progress.info("Syncing host configuration (timezone, locale, DNS)")
    try:
        await ctx.host_config_sync.sync_container(ctx.name)
    except Exception as e:
        ctx.progress.warning(f"Failed to sync host configuration: {e}")
