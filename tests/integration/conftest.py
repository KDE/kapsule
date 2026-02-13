# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pytest configuration for Kapsule integration tests."""

from __future__ import annotations

import asyncio
import os

import pytest

# ---------------------------------------------------------------------------
# VM configuration
# ---------------------------------------------------------------------------

TEST_VM = os.environ.get("KAPSULE_TEST_VM", "192.168.100.129")
SSH_OPTS = [
    "-o", "ConnectTimeout=5",
    "-o", "StrictHostKeyChecking=no",
    "-o", "LogLevel=ERROR",
]


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
async def ssh_run_on_vm(*cmd: str) -> asyncio.subprocess.Process:
    """Run a command on the test VM over SSH and return the process.

    The caller can ``await proc.wait()`` or read stdout/stderr as needed.
    """
    full_cmd = ["ssh", *SSH_OPTS, TEST_VM, *cmd]
    return await asyncio.create_subprocess_exec(
        *full_cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
