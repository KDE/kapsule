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
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import IntEnum
from typing import (
    Annotated,
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Concatenate,
    ParamSpec,
    TypeVar,
)

from dbus_fast.service import ServiceInterface
from dbus_fast.aio import MessageBus

from .dbus_types import (
    DBusStr,
    DBusBool,
    dbus_property,
    method,
    signal,
    PropertyAccess,
)


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

    # -------------------------------------------------------------------------
    # Signals
    # -------------------------------------------------------------------------

    @signal()
    def Message(
        self,
        message_type: int,
        message: DBusStr,
        indent_level: int,
    ) -> Annotated[list[int | str], "isi"]:
        """Emitted for progress messages.

        Args:
            message_type: Type (0=info, 1=success, 2=warning, 3=error, 4=dim, 5=hint)
            message: The message text
            indent_level: Indentation level for hierarchical display
        """
        return [message_type, message, indent_level]

    @signal()
    def ProgressStarted(
        self,
        progress_id: DBusStr,
        description: DBusStr,
        total: int,
        indent_level: int,
    ) -> Annotated[list[str | int], "ssii"]:
        """Emitted when a progress bar starts.

        Args:
            progress_id: Unique ID for this progress bar
            description: What's being tracked (e.g., "Downloading image...")
            total: Total units (-1 for indeterminate)
            indent_level: Indentation level
        """
        return [progress_id, description, total, indent_level]

    @signal()
    def ProgressUpdate(
        self,
        progress_id: DBusStr,
        current: int,
        rate: float,
    ) -> Annotated[list[str | int | float], "sid"]:
        """Emitted to update a progress bar.

        Args:
            progress_id: Progress bar to update
            current: Current progress value
            rate: Rate of progress (for ETA calculation)
        """
        return [progress_id, current, rate]

    @signal()
    def ProgressCompleted(
        self,
        progress_id: DBusStr,
        success: DBusBool,
        message: DBusStr,
    ) -> Annotated[list[str | bool], "sbs"]:
        """Emitted when a progress bar completes.

        Args:
            progress_id: Progress bar that completed
            success: Whether it succeeded
            message: Optional completion message (replaces bar)
        """
        return [progress_id, success, message]

    @signal()
    def Completed(
        self,
        success: DBusBool,
        message: DBusStr,
    ) -> Annotated[list[bool | str], "bs"]:
        """Emitted when this operation finishes.

        Args:
            success: Whether the operation succeeded
            message: Error message if failed, empty if succeeded
        """
        return [success, message]

    # -------------------------------------------------------------------------
    # Methods
    # -------------------------------------------------------------------------

    @method()
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
        print(f"[Operation {self._op_id}] Emitting Completed signal: success={success}, message={message!r}")
        result = self.Completed(success, message)
        print(f"[Operation {self._op_id}] Signal emitted, result={result}")


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

    def complete(self, success: bool = True, message: str = "") -> None:
        """Complete and remove the progress bar.

        Args:
            success: Whether the operation succeeded
            message: Optional message to display (replaces the bar)
        """
        self._operation.ProgressCompleted(self.progress_id, success, message)


@dataclass
class OperationReporter:
    """Injected into operations for progress reporting.

    This is the main interface operations use to report progress.
    Messages are emitted as D-Bus signals on the operation object.

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
        self._operation.Message(
            int(MessageType.INFO),
            message,
            indent if indent is not None else self._indent,
        )

    def success(self, message: str, indent: int | None = None) -> None:
        """Emit a success message."""
        self._operation.Message(
            int(MessageType.SUCCESS),
            message,
            indent if indent is not None else self._indent,
        )

    def warning(self, message: str, indent: int | None = None) -> None:
        """Emit a warning message."""
        self._operation.Message(
            int(MessageType.WARNING),
            message,
            indent if indent is not None else self._indent,
        )

    def error(self, message: str, indent: int | None = None) -> None:
        """Emit an error message."""
        self._operation.Message(
            int(MessageType.ERROR),
            message,
            indent if indent is not None else self._indent,
        )

    def dim(self, message: str, indent: int | None = None) -> None:
        """Emit a dimmed/secondary message."""
        self._operation.Message(
            int(MessageType.DIM),
            message,
            indent if indent is not None else self._indent,
        )

    def hint(self, message: str, indent: int | None = None) -> None:
        """Emit a hint message."""
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
    ) -> AsyncIterator[ProgressBar]:
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

    def indented(self, levels: int = 1) -> "OperationReporter":
        """Return a reporter with increased indent level.

        Use for sub-operations that should appear nested in the output.

        Args:
            levels: Number of indent levels to add
        """
        return OperationReporter(
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

    _operations: dict[str, RunningOperation] = field(default_factory=_make_operations_dict)
    _bus: MessageBus | None = None
    _cleanup_delay: float = 5.0  # Seconds to keep completed operations

    def set_bus(self, bus: MessageBus) -> None:
        """Set the message bus for exporting operation objects."""
        print(f"[OperationTracker] set_bus called with bus={bus}")
        self._bus = bus

    def add(self, op: RunningOperation) -> None:
        """Register a running operation and export it to D-Bus."""
        self._operations[op.id] = op
        if self._bus:
            print(f"[OperationTracker] Exporting operation {op.id} to {op.interface.object_path}")
            self._bus.export(op.interface.object_path, op.interface)
            print(f"[OperationTracker] Export complete for {op.id}")

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
            reporter = OperationReporter(
                _operation=op_interface,
            )

            # Run the operation in a task so we return the path immediately
            async def run_operation() -> None:
                print(f"[Operation {op_id}] Starting execution of {operation_type}")
                try:
                    await func(self, reporter, *args, **kwargs)
                    print(f"[Operation {op_id}] Completed successfully")
                    op_interface.mark_completed(True, "")
                except asyncio.CancelledError:
                    # Operation was cancelled
                    print(f"[Operation {op_id}] Cancelled")
                    op_interface.mark_completed(False, "Operation cancelled")
                except OperationError as e:
                    # Expected errors - user-friendly message
                    print(f"[Operation {op_id}] OperationError: {e}")
                    op_interface.mark_completed(False, str(e))
                except Exception as e:
                    # Unexpected errors - log and report
                    import traceback

                    print(f"[Operation {op_id}] Exception: {e}")
                    traceback.print_exc()
                    op_interface.mark_completed(False, f"Internal error: {e}")
                finally:
                    # Remove from tracker after completion
                    if hasattr(self, "_tracker"):
                        print(f"[Operation {op_id}] Removing from tracker")
                        self._tracker.remove(op_id)

            # IMPORTANT: Export the operation to D-Bus BEFORE starting the task
            # to avoid race conditions where clients can't connect to the operation
            # or miss signals emitted early in execution
            if hasattr(self, "_tracker"):
                # Create the task but don't start it yet (just create the Task object)
                task = asyncio.create_task(run_operation(), name=f"op-{operation_type}-{op_id[:8]}")
                op_interface.set_task(task)
                
                print(f"[Operation {op_id}] Created task, adding to tracker")
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
                print(f"[Operation {op_id}] Added to tracker")
            else:
                # No tracker - just create the task
                task = asyncio.create_task(run_operation(), name=f"op-{operation_type}-{op_id[:8]}")
                op_interface.set_task(task)

            # Return the D-Bus object path
            print(f"[Operation {op_id}] Returning path: {op_interface.object_path}")
            return op_interface.object_path

        return wrapper

    return decorator
