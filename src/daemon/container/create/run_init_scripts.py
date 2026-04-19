# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Creation pipeline step: run one-shot init scripts from the image."""

import asyncio

from ...incus_client import IncusError
from ..contexts import CreateContext
from . import create_pipeline

_INIT_DIR = "/.kapsule/init"


@create_pipeline.step(order=125)
async def run_init_scripts(ctx: CreateContext) -> None:
    """Run executable scripts in /.kapsule/init/ inside the new container.

    Images can ship one-shot initialisation scripts under ``/.kapsule/init/``.
    Each executable file in that directory is run in lexicographic order
    as root.  This happens early in the post-creation pipeline so that
    later steps (host-config sync, file capabilities, etc.) see a fully
    initialised container.

    Scripts are expected to be idempotent — if a container is somehow
    created from a snapshot that already ran them, re-running should be
    harmless.
    """
    try:
        entries = await ctx.incus.list_directory(ctx.name, _INIT_DIR)
    except IncusError:
        ctx.progress.dim("No /.kapsule/init/ directory — skipping init scripts")
        return

    if not entries:
        ctx.progress.dim("No init scripts found in /.kapsule/init/")
        return

    for script_name in entries:
        script_path = f"{_INIT_DIR}/{script_name}"
        ctx.progress.info(f"Running init script: {script_name}")
        proc = await asyncio.create_subprocess_exec(
            "incus", "exec", ctx.name, "--", script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            ctx.progress.warning(
                f"Init script {script_name} failed (rc={proc.returncode}): "
                f"{stderr.decode(errors='replace').strip()}"
            )
        else:
            ctx.progress.dim(f"Init script {script_name} completed")
