# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Operation framework for the Kapsule daemon.

Provides decorators and utilities for daemon operations that emit
progress signals over D-Bus.

Each operation is exposed as a separate D-Bus object at
/org/kde/kapsule/operations/{id}. This allows clients to:
- Subscribe to signals for only the operations they care about
- Avoid race conditions by getting the object path before work starts
- Cancel operations via a method call
"""

from __future__ import annotations

import asyncio
import functools
import itertools
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from enum import IntEnum
from typing import (
    Annotated,
    Any,
    Concatenate,
    ParamSpec,
    Protocol,
    TypeVar,
    runtime_checkable,
)

from dbus_fast.aio import MessageBus
from dbus_fast.annotations import DBusBool, DBusSignature, DBusStr
from dbus_fast.constants import PropertyAccess
from dbus_fast.service import ServiceInterface, dbus_method, dbus_property, dbus_signal

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

# Global counter for operation IDs (simpler than UUIDs, easier to debug)
_operation_counter = itertools.count(1)


class MessageType(IntEnum):
    """Message types for operation progress."""

    INFO = 0  # Regular info (blue)
    SUCCESS = 1  # Success with checkmark (green)
    WARNING = 2  # Warning (yellow)
    ERROR = 3  # Error (red)
    DIM = 4  # Muted/secondary info (gray)
    HINT = 5  # Hint for user action


class OperationError(Exception):
    """Raised for expected operation failures with user-friendly messages.

    These are caught by the operation decorator and reported cleanly
    to the user, unlike unexpected exceptions which show as internal errors.
    """

    pass


# =============================================================================
# Operation D-Bus Interface
# =============================================================================


class OperationInterface(ServiceInterface):
    """D-Bus interface for a single operation.

    Each running operation gets its own D-Bus object at
    /org/kde/kapsule/operations/{uuid}. This allows clients to:
    - Subscribe to signals for just this operation
    - Cancel the operation
    - Query operation status

    The interface is: org.kde.kapsule.Operation
    """

    def __init__(self, op_id: str, op_type: str, description: str, target: str):
        super().__init__("org.kde.kapsule.Operation")
        self._op_id = op_id
        self._op_type = op_type
        self._description = description
        self._target = target
        self._status = "running"
        self._error_message = ""
        self._cancel_requested = False
        self._task: asyncio.Task[None] | None = None

    @property
    def object_path(self) -> str:
        """Get the D-Bus object path for this operation."""
        return f"/org/kde/kapsule/operations/{self._op_id}"

    def set_task(self, task: asyncio.Task[None]) -> None:
        """Set the asyncio task for this operation (for cancellation)."""
        self._task = task

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> DBusStr:
        """Unique identifier for this operation."""
        return self._op_id

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> DBusStr:
        """Operation type (create, delete, start, stop, etc.)."""
        return self._op_type

    @dbus_property(access=PropertyAccess.READ)
    def Description(self) -> DBusStr:
        """Human-readable description of this operation."""
        return self._description

    @dbus_property(access=PropertyAccess.READ)
    def Target(self) -> DBusStr:
        """Target of the operation (usually container name)."""
        return self._target

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> DBusStr:
        """Current status: running, completed, failed, cancelled."""
        return self._status

    @dbus_property(access=PropertyAccess.READ)
    def ErrorMessage(self) -> DBusStr:
        """Error message if the operation failed, empty otherwise."""
        return self._error_message

    # -------------------------------------------------------------------------
    # Signals
    # -------------------------------------------------------------------------

    @dbus_signal()
    def Message(
        self,
        message_type: int,
        message: DBusStr,
        indent_level: int,
    ) -> Annotated[tuple[int, str, int], DBusSignature("isi")]:
        """Emitted for progress messages.

        Args:
            message_type: Type (0=info, 1=success, 2=warning, 3=error, 4=dim, 5=hint)
            message: The message text
            indent_level: Indentation level for hierarchical display
        """
        return (message_type, message, indent_level)

    @dbus_signal()
    def ProgressStarted(
        self,
        progress_id: DBusStr,
        description: DBusStr,
        total: int,
        indent_level: int,
    ) -> Annotated[tuple[str, str, int, int], DBusSignature("ssii")]:
        """Emitted when a progress bar starts.

        Args:
            progress_id: Unique ID for this progress bar
            description: What's being tracked (e.g., "Downloading image...")
            total: Total units (-1 for indeterminate)
            indent_level: Indentation level
        """
        return (progress_id, description, total, indent_level)

    @dbus_signal()
    def ProgressUpdate(
        self,
        progress_id: DBusStr,
        current: int,
        rate: float,
    ) -> Annotated[tuple[str, int, float], DBusSignature("sid")]:
        """Emitted to update a progress bar.

        Args:
            progress_id: Progress bar to update
            current: Current progress value
            rate: Rate of progress (for ETA calculation)
        """
        return (progress_id, current, rate)

    @dbus_signal()
    def ProgressTextUpdate(
        self,
        progress_id: DBusStr,
        text: DBusStr,
    ) -> Annotated[tuple[str, str], DBusSignature("ss")]:
        """Emitted to update a progress bar with raw text (e.g. download progress).

        Args:
            progress_id: Progress bar to update
            text: Raw progress text from Incus (e.g. "rootfs: 42% (49.2MB/s)")
        """
        return (progress_id, text)

    @dbus_signal()
    def ProgressCompleted(
        self,
        progress_id: DBusStr,
        success: DBusBool,
        message: DBusStr,
    ) -> Annotated[tuple[str, bool, str], DBusSignature("sbs")]:
        """Emitted when a progress bar completes.

        Args:
            progress_id: Progress bar that completed
            success: Whether it succeeded
            message: Optional completion message (replaces bar)
        """
        return (progress_id, success, message)

    @dbus_signal()
    def Completed(
        self,
        success: DBusBool,
        message: DBusStr,
    ) -> Annotated[tuple[bool, str], DBusSignature("bs")]:
        """Emitted when this operation finishes.

        Args:
            success: Whether the operation succeeded
            message: Error message if failed, empty if succeeded
        """
        return (success, message)

    # -------------------------------------------------------------------------
    # Methods
    # -------------------------------------------------------------------------

    @dbus_method()
    def Cancel(self) -> DBusBool:
        """Request cancellation of this operation.

        Returns True if cancellation was requested, False if already
        completed or cancellation not supported.
        """
        if self._status != "running":
            return False

        self._cancel_requested = True

        # If we have a task, cancel it
        if self._task and not self._task.done():
            self._task.cancel()

        return True

    # -------------------------------------------------------------------------
    # Internal helpers (not exposed over D-Bus)
    # -------------------------------------------------------------------------

    def is_cancel_requested(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancel_requested

    def mark_completed(self, success: bool, message: str = "") -> None:
        """Mark the operation as completed and emit the Completed signal."""
        self._status = "completed" if success else "failed"
        if self._cancel_requested and not success:
            self._status = "cancelled"
        self._error_message = message

        # Emit PropertiesChanged so clients polling Status (rather than
        # listening for the Completed signal) are notified immediately.
        self.emit_properties_changed(
            {"Status": self._status, "ErrorMessage": self._error_message}
        )

        if success:
            logger.info("Operation %s completed successfully", self._op_id)
        else:
            logger.error("Operation %s failed: %s", self._op_id, message)

        self.Completed(success, message)


# =============================================================================
# Progress Bar and Reporter
# =============================================================================


@dataclass
class ProgressBar:
    """Handle for an active progress bar."""

    progress_id: str
    _operation: OperationInterface

    def update(self, current: int, rate: float = 0.0) -> None:
        """Update progress bar position.

        Args:
            current: Current progress value (bytes, items, etc.)
            rate: Rate of progress (bytes/sec, etc.) for ETA calculation
        """
        self._operation.ProgressUpdate(self.progress_id, current, rate)

    def update_text(self, text: str) -> None:
        """Update progress bar with raw text (e.g. download progress).

        Args:
            text: Raw progress text from Incus (e.g. "rootfs: 42% (49.2MB/s)")
        """
        self._operation.ProgressTextUpdate(self.progress_id, text)

    def complete(self, success: bool = True, message: str = "") -> None:
        """Complete and remove the progress bar.

        Args:
            success: Whether the operation succeeded
            message: Optional message to display (replaces the bar)
        """
        self._operation.ProgressCompleted(self.progress_id, success, message)


class NullProgressBar:
    """No-op progress bar for contexts without progress reporting."""

    def update(self, current: int, rate: float = 0.0) -> None:
        pass

    def update_text(self, text: str) -> None:
        pass

    def complete(self, success: bool = True, message: str = "") -> None:
        pass


@runtime_checkable
class OperationReporter(Protocol):
    """Protocol for progress reporting during operations.

    Consumers should type-hint against this protocol rather than
    a concrete implementation. This allows no-op reporters to be
    used in contexts where progress reporting is not needed.
    """

    @property
    def operation_id(self) -> str: ...

    def is_cancelled(self) -> bool: ...

    def info(self, message: str, indent: int | None = None) -> None: ...

    def success(self, message: str, indent: int | None = None) -> None: ...

    def warning(self, message: str, indent: int | None = None) -> None: ...

    def error(self, message: str, indent: int | None = None) -> None: ...

    def dim(self, message: str, indent: int | None = None) -> None: ...

    def hint(self, message: str, indent: int | None = None) -> None: ...

    def start_progress(
        self,
        description: str,
        total: int = -1,
        indent: int | None = None,
    ) -> ProgressBar | NullProgressBar: ...

    def track(
        self,
        description: str,
        total: int = -1,
        indent: int | None = None,
        success_message: str = "",
    ) -> AbstractAsyncContextManager[ProgressBar | NullProgressBar]: ...

    def indented(self, levels: int = 1) -> OperationReporter: ...


class NullOperationReporter:
    """No-op implementation of OperationReporter.

    Used in contexts where progress reporting is not needed,
    such as internal operations that are not exposed over D-Bus.
    """

    @property
    def operation_id(self) -> str:
        return ""

    def is_cancelled(self) -> bool:
        return False

    def info(self, message: str, indent: int | None = None) -> None:
        pass

    def success(self, message: str, indent: int | None = None) -> None:
        pass

    def warning(self, message: str, indent: int | None = None) -> None:
        pass

    def error(self, message: str, indent: int | None = None) -> None:
        pass

    def dim(self, message: str, indent: int | None = None) -> None:
        pass

    def hint(self, message: str, indent: int | None = None) -> None:
        pass

    def start_progress(
        self,
        description: str,
        total: int = -1,
        indent: int | None = None,
    ) -> NullProgressBar:
        return NullProgressBar()

    @asynccontextmanager
    async def track(
        self,
        description: str,
        total: int = -1,
        indent: int | None = None,
        success_message: str = "",
    ) -> AsyncGenerator[NullProgressBar, None]:
        yield NullProgressBar()

    def indented(self, levels: int = 1) -> NullOperationReporter:
        return self


@dataclass
class DBusOperationReporter:
    """D-Bus-backed implementation of OperationReporter.

    Reports progress by emitting D-Bus signals on the operation object.

    Usage in operation methods:
        async def create_container(self, progress: OperationReporter, name: str, ...):
            progress.info(f"Image: {image}")

            async with progress.track("Downloading image...", total=size) as bar:
                for chunk in download():
                    bar.update(downloaded)

            progress.success("Image downloaded")
    """

    _operation: OperationInterface
    _indent: int = 1  # Default indent for messages within operation

    @property
    def operation_id(self) -> str:
        """Get the operation ID."""
        return self._operation.Id

    def is_cancelled(self) -> bool:
        """Check if the operation has been cancelled.

        Operations should check this periodically and stop gracefully if True.
        """
        return self._operation.is_cancel_requested()

    def info(self, message: str, indent: int | None = None) -> None:
        """Emit an info message."""
        logger.info("[op %s] %s", self.operation_id, message)
        self._operation.Message(
            int(MessageType.INFO),
            message,
            indent if indent is not None else self._indent,
        )

    def success(self, message: str, indent: int | None = None) -> None:
        """Emit a success message."""
        logger.info("[op %s] %s", self.operation_id, message)
        self._operation.Message(
            int(MessageType.SUCCESS),
            message,
            indent if indent is not None else self._indent,
        )

    def warning(self, message: str, indent: int | None = None) -> None:
        """Emit a warning message."""
        logger.warning("[op %s] %s", self.operation_id, message)
        self._operation.Message(
            int(MessageType.WARNING),
            message,
            indent if indent is not None else self._indent,
        )

    def error(self, message: str, indent: int | None = None) -> None:
        """Emit an error message."""
        logger.error("[op %s] %s", self.operation_id, message)
        self._operation.Message(
            int(MessageType.ERROR),
            message,
            indent if indent is not None else self._indent,
        )

    def dim(self, message: str, indent: int | None = None) -> None:
        """Emit a dimmed/secondary message."""
        logger.debug("[op %s] %s", self.operation_id, message)
        self._operation.Message(
            int(MessageType.DIM),
            message,
            indent if indent is not None else self._indent,
        )

    def hint(self, message: str, indent: int | None = None) -> None:
        """Emit a hint message."""
        logger.info("[op %s] %s", self.operation_id, message)
        self._operation.Message(
            int(MessageType.HINT),
            message,
            indent if indent is not None else self._indent,
        )

    def start_progress(
        self,
        description: str,
        total: int = -1,
        indent: int | None = None,
    ) -> ProgressBar:
        """Start a progress bar.

        Remember to call .complete() when done, or use the track() context manager.

        Args:
            description: What's being tracked (e.g., "Downloading image...")
            total: Total units, or -1 for indeterminate
            indent: Indent level for display
        """
        progress_id = str(next(_operation_counter))
        logger.info(
            "[op %s] Progress started: %s (total=%s)",
            self.operation_id,
            description,
            total if total >= 0 else "indeterminate",
        )
        self._operation.ProgressStarted(
            progress_id,
            description,
            total,
            indent if indent is not None else self._indent,
        )
        return ProgressBar(progress_id, self._operation)

    @asynccontextmanager
    async def track(
        self,
        description: str,
        total: int = -1,
        indent: int | None = None,
        success_message: str = "",
    ) -> AsyncGenerator[ProgressBar, None]:
        """Context manager for progress tracking.

        Usage:
            async with progress.track("Downloading...", total=size) as bar:
                for chunk in data:
                    bar.update(current)

        Args:
            description: What's being tracked
            total: Total units, or -1 for indeterminate
            indent: Indent level
            success_message: Optional message on success (replaces bar)
        """
        bar = self.start_progress(description, total, indent)
        try:
            yield bar
            bar.complete(success=True, message=success_message)
        except Exception:
            bar.complete(success=False)
            raise

    def indented(self, levels: int = 1) -> DBusOperationReporter:
        """Return a reporter with increased indent level.

        Use for sub-operations that should appear nested in the output.

        Args:
            levels: Number of indent levels to add
        """
        return DBusOperationReporter(
            _operation=self._operation,
            _indent=self._indent + levels,
        )


# =============================================================================
# Operation Tracking
# =============================================================================


@dataclass
class RunningOperation:
    """Tracks a running operation."""

    id: str
    operation_type: str
    target: str
    task: asyncio.Task[None]
    interface: OperationInterface


def _make_operations_dict() -> dict[str, RunningOperation]:
    """Factory for operations dict with explicit type."""
    return {}


@dataclass
class OperationTracker:
    """Tracks all running operations in the daemon."""

    _operations: dict[str, RunningOperation] = field(
        default_factory=_make_operations_dict
    )
    _bus: MessageBus | None = None
    _cleanup_delay: float = 5.0  # Seconds to keep completed operations

    def set_bus(self, bus: MessageBus) -> None:
        """Set the message bus for exporting operation objects."""
        logger.debug("OperationTracker: bus set")
        self._bus = bus

    def add(self, op: RunningOperation) -> None:
        """Register a running operation and export it to D-Bus."""
        self._operations[op.id] = op
        if self._bus:
            logger.debug(
                "Exporting operation %s to %s", op.id, op.interface.object_path
            )
            self._bus.export(op.interface.object_path, op.interface)

    def remove(self, op_id: str) -> None:
        """Remove a completed operation from tracking.

        Note: The D-Bus object is removed after a delay to allow clients
        to read the final state.
        """
        op = self._operations.pop(op_id, None)
        if op and self._bus:
            # Schedule delayed unexport
            asyncio.create_task(self._delayed_unexport(op.interface))

    async def _delayed_unexport(self, interface: OperationInterface) -> None:
        """Unexport an operation after a delay."""
        await asyncio.sleep(self._cleanup_delay)
        if self._bus:
            try:
                self._bus.unexport(interface.object_path, interface)
            except Exception:
                pass  # Already unexported or bus closed

    def get(self, op_id: str) -> RunningOperation | None:
        """Get an operation by ID."""
        return self._operations.get(op_id)

    def list_all(self) -> list[RunningOperation]:
        """List all running operations."""
        return list(self._operations.values())

    def list_paths(self) -> list[str]:
        """List D-Bus object paths of all running operations."""
        return [op.interface.object_path for op in self._operations.values()]


# =============================================================================
# Operation Decorator
# =============================================================================


def operation(
    operation_type: str,
    description: str,
    target_param: str = "name",
) -> Callable[
    [Callable[Concatenate[Any, OperationReporter, P], Awaitable[None]]],
    Callable[Concatenate[Any, P], Awaitable[str]],
]:
    """Decorator for daemon operations with D-Bus object lifecycle.

    Wraps an async method to:
    - Generate a unique operation ID
    - Create a D-Bus object for the operation at /org/kde/kapsule/operations/{id}
    - Inject an OperationReporter as the first parameter (after self)
    - Run the operation in a background task
    - Return the D-Bus object path immediately (client subscribes to signals)
    - Emit Completed signal on the operation object when done
    - Handle OperationError for user-friendly error messages

    Args:
        operation_type: Type identifier (e.g., "create", "delete", "start")
        description: Template string with {param} placeholders for kwargs
        target_param: Name of the parameter that represents the target

    Example:
        @operation("create", "Creating container: {name}")
        async def create_container(
            self,
            progress: OperationReporter,  # Auto-injected
            name: str,
            image: str,
        ) -> None:
            progress.info(f"Image: {image}")
            ...

    Returns:
        The D-Bus object path: /org/kde/kapsule/operations/{id}
    """

    def decorator(
        func: Callable[Concatenate[Any, OperationReporter, P], Awaitable[None]],
    ) -> Callable[Concatenate[Any, P], Awaitable[str]]:
        @functools.wraps(func)
        async def wrapper(self: Any, *args: P.args, **kwargs: P.kwargs) -> str:
            # Generate operation ID using incrementing counter
            op_id = str(next(_operation_counter))

            # Build description from template
            desc = description.format(**kwargs)

            # Get target from kwargs
            target = str(kwargs.get(target_param, ""))

            # Create the operation D-Bus interface
            op_interface = OperationInterface(op_id, operation_type, desc, target)

            # Create the reporter that wraps the interface
            reporter = DBusOperationReporter(
                _operation=op_interface,
            )

            # Run the operation in a task so we return the path immediately
            async def run_operation() -> None:
                logger.info(
                    "Operation %s (%s) starting: %s", op_id, operation_type, desc
                )
                try:
                    await func(self, reporter, *args, **kwargs)
                    op_interface.mark_completed(True, "")
                except asyncio.CancelledError:
                    # Operation was cancelled
                    logger.info("Operation %s cancelled", op_id)
                    op_interface.mark_completed(False, "Operation cancelled")
                except OperationError as e:
                    # Expected errors - user-friendly message
                    logger.warning("Operation %s failed: %s", op_id, e)
                    op_interface.mark_completed(False, str(e))
                except Exception as e:
                    # Unexpected errors - log full traceback
                    logger.exception(
                        "Operation %s (%s) raised an unexpected exception",
                        op_id,
                        operation_type,
                    )
                    op_interface.mark_completed(False, f"Internal error: {e}")
                finally:
                    # Remove from tracker after completion
                    if hasattr(self, "_tracker"):
                        self._tracker.remove(op_id)

            # IMPORTANT: Export the operation to D-Bus BEFORE starting the task
            # to avoid race conditions where clients can't connect to the operation
            # or miss signals emitted early in execution
            if hasattr(self, "_tracker"):
                # Create the task but don't start it yet (just create the Task object)
                task = asyncio.create_task(
                    run_operation(), name=f"op-{operation_type}-{op_id[:8]}"
                )
                op_interface.set_task(task)

                # Export to D-Bus before the task starts running
                self._tracker.add(
                    RunningOperation(
                        id=op_id,
                        operation_type=operation_type,
                        target=target,
                        task=task,
                        interface=op_interface,
                    )
                )
            else:
                # No tracker - just create the task
                task = asyncio.create_task(
                    run_operation(), name=f"op-{operation_type}-{op_id[:8]}"
                )
                op_interface.set_task(task)

            # Return the D-Bus object path
            logger.debug("Operation %s exposed at %s", op_id, op_interface.object_path)
            return op_interface.object_path

        return wrapper

    return decorator
