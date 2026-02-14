#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 KDE Community
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run PrepareEnter logic in the foreground with explicit phase prints.

This replicates the daemon's PrepareEnter steps without D-Bus dispatch,
which is useful for isolating crashes/hangs in CI.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.daemon.container_service import ContainerService
from src.daemon.incus_client import IncusClient

if TYPE_CHECKING:
    from src.daemon.service import KapsuleManagerInterface


class _NoopInterface:
    """Placeholder interface for ContainerService in standalone debug mode."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Kapsule PrepareEnter phases directly in foreground",
    )
    parser.add_argument(
        "container",
        nargs="?",
        default="",
        help="Container name (empty uses default from config)",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run inside the container (prefix with --)",
    )
    parser.add_argument(
        "--uid",
        type=int,
        default=os.getuid(),
        help="UID to use for PrepareEnter (default: current UID)",
    )
    parser.add_argument(
        "--gid",
        type=int,
        default=os.getgid(),
        help="GID to use for PrepareEnter (default: current GID)",
    )
    parser.add_argument(
        "--socket",
        default="/var/lib/incus/unix.socket",
        help="Incus Unix socket path",
    )
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]

    container_name = args.container or None

    print("=== PrepareEnter foreground debug ===", flush=True)
    print(
        f"uid={args.uid} gid={args.gid} container={container_name or '(default)'} command={' '.join(command) if command else '(shell)'}",
        flush=True,
    )

    incus = IncusClient(socket_path=args.socket)
    service = ContainerService(cast("KapsuleManagerInterface", _NoopInterface()), incus)

    try:
        success, message, exec_args = await service.prepare_enter_foreground_debug(
            uid=args.uid,
            gid=args.gid,
            container_name=container_name,
            command=command,
            env=dict(os.environ),
        )
    finally:
        await incus.close()

    print("=== PrepareEnter result ===", flush=True)
    print(f"success={success}", flush=True)
    print(f"message={message}", flush=True)
    if exec_args:
        print(f"exec_args={' '.join(exec_args)}", flush=True)

    return 0 if success else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
