# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Integration tests for operation progress signals.

These tests verify that D-Bus signals are emitted correctly during
long-running operations like container creation.

The daemon exposes operations as D-Bus objects at
/org/kde/kapsule/operations/{id}.  Each object implements the
org.kde.kapsule.Operation interface which emits:

    Message(message_type: i, message: s, indent_level: i)
    ProgressStarted(progress_id: s, description: s, total: i, indent: i)
    ProgressUpdate(progress_id: s, current: i, rate: d)
    ProgressCompleted(progress_id: s, success: b, message: s)
    Completed(success: b, message: s)

The root object at /org/kde/kapsule implements
org.freedesktop.DBus.ObjectManager, so InterfacesAdded /
InterfacesRemoved signals fire when operations are exported /
unexported.
"""

from __future__ import annotations

import asyncio
import pytest
from dataclasses import dataclass, field

from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Message, MessageType, Variant

from conftest import ssh_run_on_vm


DBUS_NAME = "org.kde.kapsule"
DBUS_PATH = "/org/kde/kapsule"
DBUS_INTERFACE = "org.kde.kapsule.Manager"

CONTAINER_NAME = "test-signals"
TEST_IMAGE = "images:alpine/edge"


# ---------------------------------------------------------------------------
# Signal collector
# ---------------------------------------------------------------------------

@dataclass
class SignalCollector:
    """Collects D-Bus signals during a test."""

    # ObjectManager signals (operation lifecycle)
    interfaces_added: list[tuple[str, dict]] = field(default_factory=list)
    interfaces_removed: list[tuple[str, list[str]]] = field(default_factory=list)

    # Operation-level signals
    progress_started: list[tuple[str, str, str, int, int]] = field(default_factory=list)
    progress_updates: list[tuple[str, str, int, float]] = field(default_factory=list)
    progress_completed: list[tuple[str, str, bool, str]] = field(default_factory=list)
    completed: list[tuple[str, bool, str]] = field(default_factory=list)
    messages: list[tuple[str, int, str, int]] = field(default_factory=list)


def make_signal_handler(collector: SignalCollector, operation_path: str | None = None):
    """Build a message handler that dispatches signals into *collector*.

    If *operation_path* is given, operation-level signals are only
    recorded for that path.
    """

    def handler(msg: Message) -> None:
        if msg.message_type != MessageType.SIGNAL:
            return

        # ObjectManager signals (global, on the root path)
        if msg.member == "InterfacesAdded":
            path, ifaces = msg.body
            collector.interfaces_added.append((path, ifaces))
        elif msg.member == "InterfacesRemoved":
            path, ifaces = msg.body
            collector.interfaces_removed.append((path, ifaces))

        # Operation-level signals (filtered by path when given)
        if operation_path is not None and msg.path != operation_path:
            return
        if msg.member == "Message":
            mtype, text, indent = msg.body
            collector.messages.append((msg.path, mtype, text, indent))
        elif msg.member == "ProgressStarted":
            pid, desc, total, indent = msg.body
            collector.progress_started.append((msg.path, pid, desc, total, indent))
        elif msg.member == "ProgressUpdate":
            pid, current, rate = msg.body
            collector.progress_updates.append((msg.path, pid, current, rate))
        elif msg.member == "ProgressCompleted":
            pid, success, text = msg.body
            collector.progress_completed.append((msg.path, pid, success, text))
        elif msg.member == "Completed":
            success, text = msg.body
            collector.completed.append((msg.path, success, text))

    return handler


# ---------------------------------------------------------------------------
# D-Bus helpers
# ---------------------------------------------------------------------------

async def cleanup_container(name: str) -> None:
    """Force delete a container on the VM, ignoring errors."""
    proc = await ssh_run_on_vm("incus", "delete", name, "--force")
    await proc.wait()


async def subscribe_kapsule_signals(bus: MessageBus) -> None:
    """Add a D-Bus match rule for all signals from the kapsule daemon."""
    await bus.call(
        Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="AddMatch",
            signature="s",
            body=[f"type='signal',sender='{DBUS_NAME}'"],
        )
    )


async def call_create_container(
    bus: MessageBus, name: str, image: str
) -> str:
    """Call CreateContainer via D-Bus and return operation path."""
    reply = await bus.call(
        Message(
            destination=DBUS_NAME,
            path=DBUS_PATH,
            interface=DBUS_INTERFACE,
            member="CreateContainer",
            signature="ssa{sv}",
            body=[name, image, {}],
        )
    )
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(f"CreateContainer failed: {reply.body}")
    return reply.body[0]  # Operation object path


async def call_delete_container(
    bus: MessageBus, name: str, force: bool = True
) -> str:
    """Call DeleteContainer via D-Bus and return operation path."""
    reply = await bus.call(
        Message(
            destination=DBUS_NAME,
            path=DBUS_PATH,
            interface=DBUS_INTERFACE,
            member="DeleteContainer",
            signature="sb",
            body=[name, force],
        )
    )
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(f"DeleteContainer failed: {reply.body}")
    return reply.body[0]


async def wait_for_completed(
    collector: SignalCollector,
    op_path: str,
    timeout: float = 20,
) -> tuple[bool, str]:
    """Wait until a Completed signal arrives for *op_path*."""
    elapsed = 0.0
    step = 0.25
    while elapsed < timeout:
        for path, success, message in collector.completed:
            if path == op_path:
                return success, message
        await asyncio.sleep(step)
        elapsed += step
    raise TimeoutError(f"No Completed signal for {op_path} within {timeout}s")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def bus():
    """Create a D-Bus connection."""
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    yield bus
    bus.disconnect()


@pytest.fixture
async def collector():
    """Create a signal collector."""
    return SignalCollector()


@pytest.fixture(autouse=True)
async def cleanup():
    """Clean up test container before and after each test."""
    await cleanup_container(CONTAINER_NAME)
    yield
    await cleanup_container(CONTAINER_NAME)


# =========================================================================
# Tests — operation lifecycle
# =========================================================================


class TestOperationSignals:
    """Tests for operation lifecycle signals (via ObjectManager)."""

    async def test_create_emits_interfaces_added(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """Creating a container should emit InterfacesAdded for the
        operation object."""

        await subscribe_kapsule_signals(bus)
        bus.add_message_handler(make_signal_handler(collector))

        op_path = await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
        assert op_path.startswith("/org/kde/kapsule/operations/")

        # Wait for the operation to finish
        await wait_for_completed(collector, op_path)

        # InterfacesAdded should have fired for our operation
        our_adds = [
            (path, ifaces) for path, ifaces in collector.interfaces_added
            if path == op_path
        ]
        assert len(our_adds) >= 1, "No InterfacesAdded for our operation"
        assert "org.kde.kapsule.Operation" in our_adds[0][1]

    async def test_completed_signal_on_success(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """A successful create should emit Completed(true, '')."""

        await subscribe_kapsule_signals(bus)
        bus.add_message_handler(make_signal_handler(collector))

        op_path = await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
        success, message = await wait_for_completed(collector, op_path)

        assert success is True, f"Expected success but got: {message}"

    async def test_interfaces_removed_after_completion(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """The operation object should be unexported after completion."""

        await subscribe_kapsule_signals(bus)
        bus.add_message_handler(make_signal_handler(collector))

        op_path = await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
        await wait_for_completed(collector, op_path)

        # Wait for the delayed unexport (daemon keeps objects for a few seconds)
        await asyncio.sleep(8)

        our_removals = [
            path for path, _ in collector.interfaces_removed
            if path == op_path
        ]
        assert len(our_removals) >= 1, "Operation was not unexported"


# =========================================================================
# Tests — progress signals
# =========================================================================


class TestProgressSignals:
    """Tests for operation progress signals on operation objects."""

    async def test_create_emits_messages(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """Container creation should emit Message signals with
        meaningful text."""

        await subscribe_kapsule_signals(bus)

        op_path = await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
        bus.add_message_handler(make_signal_handler(collector, op_path))

        await wait_for_completed(collector, op_path)

        assert len(collector.messages) > 0, "No Message signals received"

        texts = [m[2] for m in collector.messages if m[2] and m[2].strip()]
        assert len(texts) > 0, "All Message texts were empty"

    async def test_create_progress_increases(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """If ProgressUpdate signals are emitted, values should increase
        monotonically within each progress bar."""

        await subscribe_kapsule_signals(bus)

        op_path = await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
        bus.add_message_handler(make_signal_handler(collector, op_path))

        await wait_for_completed(collector, op_path)

        # ProgressUpdate signals are optional (only emitted for downloads
        # or other measurable work).  If present, verify ordering.
        if len(collector.progress_updates) > 0:
            by_id: dict[str, list[int]] = {}
            for _op, pid, cur, _rate in collector.progress_updates:
                by_id.setdefault(pid, []).append(cur)

            for pid, values in by_id.items():
                for i in range(1, len(values)):
                    assert values[i] >= values[i - 1], (
                        f"Progress decreased for {pid}: "
                        f"{values[i - 1]} -> {values[i]}"
                    )

    async def test_completed_signal_received(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """The Completed signal should always fire when the operation
        finishes."""

        await subscribe_kapsule_signals(bus)

        op_path = await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
        bus.add_message_handler(make_signal_handler(collector, op_path))

        success, _ = await wait_for_completed(collector, op_path)
        assert success is True


# =========================================================================
# Tests — error handling
# =========================================================================


class TestErrorHandling:
    """Tests for error signal behavior."""

    async def test_delete_nonexistent_reports_failure(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """Deleting a non-existent container should produce a Completed
        signal with success=False."""

        await subscribe_kapsule_signals(bus)
        bus.add_message_handler(make_signal_handler(collector))

        # DeleteContainer returns an operation path (async pattern),
        # so the error surfaces via the Completed signal.
        op_path = await call_delete_container(bus, "nonexistent-container-xyz")
        success, message = await wait_for_completed(collector, op_path)

        assert success is False, "Delete of nonexistent container should fail"
        assert message, "Error message should not be empty"

    async def test_create_duplicate_reports_failure(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """Creating a container with an existing name should fail via
        the Completed signal."""

        await subscribe_kapsule_signals(bus)
        bus.add_message_handler(make_signal_handler(collector))

        # Create the first container successfully
        first_op = await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
        success, _ = await wait_for_completed(collector, first_op)
        assert success is True, "First create should succeed"

        # Attempt duplicate — should return an op path that fails
        dup_op = await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
        success, message = await wait_for_completed(collector, dup_op)

        assert success is False, "Duplicate create should fail"
        assert message, "Error message for duplicate should not be empty"
