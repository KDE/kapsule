# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pytest configuration for Kapsule integration tests."""

from __future__ import annotations

import asyncio
import os
import shlex

import pytest

# ---------------------------------------------------------------------------
# Target configuration
# ---------------------------------------------------------------------------

TEST_TARGET = os.environ.get("KAPSULE_TEST_TARGET", "local")
SSH_TARGET = os.environ.get("KAPSULE_TEST_SSH_TARGET")
SSH_OPTS = [
    "-o",
    "ConnectTimeout=5",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "LogLevel=ERROR",
]


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )


async def ssh_run_on_vm(*cmd: str) -> asyncio.subprocess.Process:
    """Run a command on the selected target and return the process.

    The caller can ``await proc.wait()`` or read stdout/stderr as needed.
    """
    if TEST_TARGET == "local":
        full_cmd = list(cmd)
    else:
        if SSH_TARGET is None:
            raise RuntimeError("KAPSULE_TEST_SSH_TARGET is required for SSH targets")
        extra_options = shlex.split(os.environ.get("KAPSULE_TEST_SSH_OPTIONS", ""))
        full_cmd = ["ssh", *SSH_OPTS, *extra_options, SSH_TARGET, *cmd]
    return await asyncio.create_subprocess_exec(
        *full_cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
