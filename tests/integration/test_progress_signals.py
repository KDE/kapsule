# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Integration tests for operation progress signals.

These tests verify that D-Bus signals are emitted correctly during
long-running operations like container creation.
"""

from __future__ import annotations

import asyncio
import pytest
import subprocess
from dataclasses import dataclass, field

# We use dbus-fast which should be available on the test VM
from dbus_fast.aio import MessageBus
from dbus_fast import BusType, Message, MessageType


DBUS_NAME = "org.kde.kapsule"
DBUS_PATH = "/org/kde/kapsule/Manager"
DBUS_INTERFACE = "org.kde.kapsule.Manager"

CONTAINER_NAME = "test-signals"
TEST_IMAGE = "images:alpine/edge"


@dataclass
class SignalCollector:
    """Collects D-Bus signals during a test."""

    operation_created: list[tuple[str, str, str]] = field(default_factory=list)
    operation_removed: list[tuple[str, bool]] = field(default_factory=list)
    progress_updates: list[tuple[str, int, str]] = field(default_factory=list)

    def on_operation_created(
        self, object_path: str, operation_type: str, target: str
    ) -> None:
        """Called when OperationCreated signal is received."""
        self.operation_created.append((object_path, operation_type, target))

    def on_operation_removed(self, object_path: str, success: bool) -> None:
        """Called when OperationRemoved signal is received."""
        self.operation_removed.append((object_path, success))

    def on_progress(self, operation_id: str, progress: int, description: str) -> None:
        """Called when operation progress signal is received."""
        self.progress_updates.append((operation_id, progress, description))


async def cleanup_container(name: str) -> None:
    """Force delete a container, ignoring errors."""
    proc = await asyncio.create_subprocess_exec(
        "incus", "delete", name, "--force",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def call_create_container(
    bus: MessageBus, name: str, image: str
) -> str:
    """Call CreateContainer via D-Bus and return operation path."""
    msg = Message(
        destination=DBUS_NAME,
        path=DBUS_PATH,
        interface=DBUS_INTERFACE,
        member="CreateContainer",
        signature="ssbb",
        body=[name, image, False, False],  # name, image, session_mode, dbus_mux
    )
    reply = await bus.call(msg)

    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(f"CreateContainer failed: {reply.body}")

    return reply.body[0]  # Operation object path


async def call_delete_container(
    bus: MessageBus, name: str, force: bool = True
) -> str:
    """Call DeleteContainer via D-Bus and return operation path."""
    msg = Message(
        destination=DBUS_NAME,
        path=DBUS_PATH,
        interface=DBUS_INTERFACE,
        member="DeleteContainer",
        signature="sb",
        body=[name, force],
    )
    reply = await bus.call(msg)

    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(f"DeleteContainer failed: {reply.body}")

    return reply.body[0]


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


class TestOperationSignals:
    """Tests for operation lifecycle signals."""

    async def test_create_emits_operation_created(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """Creating a container should emit OperationCreated signal."""

        # Subscribe to signals
        def on_signal(msg: Message) -> None:
            if msg.member == "OperationCreated":
                collector.on_operation_created(*msg.body)
            elif msg.member == "OperationRemoved":
                collector.on_operation_removed(*msg.body)

        bus.add_message_handler(on_signal)

        # Add match rule for our signals
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

        # Start create operation
        op_path = await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
        assert op_path.startswith("/org/kde/kapsule/operations/")

        # Wait for signals (operation should complete within timeout)
        await asyncio.sleep(10)

        # Verify OperationCreated was emitted
        assert len(collector.operation_created) >= 1, "No OperationCreated signal received"

        # Find our operation
        our_ops = [
            op for op in collector.operation_created if op[2] == CONTAINER_NAME
        ]
        assert len(our_ops) >= 1, f"No OperationCreated for {CONTAINER_NAME}"

        created_path, op_type, target = our_ops[0]
        assert created_path == op_path
        assert op_type == "create"
        assert target == CONTAINER_NAME

    async def test_operation_removed_on_completion(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """Completed operations should emit OperationRemoved signal."""

        def on_signal(msg: Message) -> None:
            if msg.member == "OperationCreated":
                collector.on_operation_created(*msg.body)
            elif msg.member == "OperationRemoved":
                collector.on_operation_removed(*msg.body)

        bus.add_message_handler(on_signal)

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

        # Create and wait for completion
        op_path = await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)

        # Wait for operation to complete and cleanup
        await asyncio.sleep(15)

        # Verify OperationRemoved was emitted
        our_removals = [op for op in collector.operation_removed if op[0] == op_path]
        assert len(our_removals) >= 1, "No OperationRemoved signal for our operation"

        removed_path, success = our_removals[0]
        assert removed_path == op_path
        assert success is True, "Operation should have succeeded"


class TestProgressSignals:
    """Tests for operation progress signals on operation objects."""

    async def test_create_progress_increases(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """Progress during create should increase over time."""

        operation_path: str | None = None

        def on_signal(msg: Message) -> None:
            nonlocal operation_path
            if msg.member == "OperationCreated":
                path, op_type, target = msg.body
                if target == CONTAINER_NAME:
                    operation_path = path
            elif msg.member == "Progress" and msg.path == operation_path:
                # Progress signal: (progress: u, description: s)
                progress, description = msg.body
                collector.on_progress(msg.path, progress, description)

        bus.add_message_handler(on_signal)

        # Subscribe to all signals from kapsule
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

        # Start operation
        await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)

        # Wait for progress signals
        await asyncio.sleep(12)

        # Verify we got progress updates
        assert len(collector.progress_updates) > 0, "No progress signals received"

        # Extract progress values
        progress_values = [p[1] for p in collector.progress_updates]

        # Progress should be monotonically increasing (or equal)
        for i in range(1, len(progress_values)):
            assert progress_values[i] >= progress_values[i - 1], (
                f"Progress decreased: {progress_values[i - 1]} -> {progress_values[i]}"
            )

        # Should reach 100% on success
        assert progress_values[-1] == 100, (
            f"Final progress should be 100, got {progress_values[-1]}"
        )

    async def test_progress_has_descriptions(
        self, bus: MessageBus, collector: SignalCollector
    ):
        """Progress signals should include meaningful descriptions."""

        operation_path: str | None = None

        def on_signal(msg: Message) -> None:
            nonlocal operation_path
            if msg.member == "OperationCreated":
                path, op_type, target = msg.body
                if target == CONTAINER_NAME:
                    operation_path = path
            elif msg.member == "Progress" and msg.path == operation_path:
                progress, description = msg.body
                collector.on_progress(msg.path, progress, description)

        bus.add_message_handler(on_signal)

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

        await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
        await asyncio.sleep(12)

        # Verify descriptions exist and are non-empty
        descriptions = [p[2] for p in collector.progress_updates]
        assert len(descriptions) > 0, "No progress signals received"

        # At least some should have meaningful descriptions
        non_empty = [d for d in descriptions if d and d.strip()]
        assert len(non_empty) > 0, "All progress descriptions were empty"


class TestErrorHandling:
    """Tests for error signal behavior."""

    async def test_delete_nonexistent_fails_gracefully(self, bus: MessageBus):
        """Deleting a non-existent container should fail with error."""

        # Try to delete non-existent container
        with pytest.raises(RuntimeError) as exc_info:
            await call_delete_container(bus, "nonexistent-container-xyz", force=True)

        # Should get a meaningful error
        assert "nonexistent" in str(exc_info.value).lower() or "not found" in str(exc_info.value).lower() or "error" in str(exc_info.value).lower()

    async def test_create_duplicate_fails(self, bus: MessageBus, collector: SignalCollector):
        """Creating a container with existing name should fail."""

        def on_signal(msg: Message) -> None:
            if msg.member == "OperationCreated":
                collector.on_operation_created(*msg.body)
            elif msg.member == "OperationRemoved":
                collector.on_operation_removed(*msg.body)

        bus.add_message_handler(on_signal)

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

        # Create first container
        await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
        await asyncio.sleep(10)

        # Try to create duplicate - should fail
        # (behavior depends on implementation - might fail at call or via signal)
        try:
            await call_create_container(bus, CONTAINER_NAME, TEST_IMAGE)
            await asyncio.sleep(5)

            # If it didn't raise, check if operation failed via signal
            failed_ops = [
                op for op in collector.operation_removed
                if op[1] is False  # success=False
            ]
            assert len(failed_ops) > 0, "Duplicate create should have failed"
        except RuntimeError:
            # Expected - immediate failure is fine too
            pass
