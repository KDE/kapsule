"""D-Bus client for communicating with the kapsule daemon.

Provides async methods for calling daemon operations and subscribing
to progress signals for real-time output.
"""

from __future__ import annotations

import asyncio
from enum import IntEnum
from typing import Callable

from dbus_fast.aio import MessageBus
from dbus_fast import BusType

from .output import out


class MessageType(IntEnum):
    """Message types from daemon operations."""

    INFO = 0
    SUCCESS = 1
    WARNING = 2
    ERROR = 3
    DIM = 4
    HINT = 5


class DaemonClient:
    """Async client for the kapsule daemon D-Bus interface.

    Handles connection, method calls, and signal subscriptions for
    displaying operation progress.
    """

    SERVICE = "org.kde.kapsule"
    OBJECT_PATH = "/org/kde/kapsule"
    INTERFACE = "org.kde.kapsule.Manager"

    def __init__(self, bus_type: BusType = BusType.SYSTEM):
        """Initialize the client.

        Args:
            bus_type: D-Bus bus type (SYSTEM or SESSION)
        """
        self._bus_type = bus_type
        self._bus: MessageBus | None = None
        self._interface = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to the D-Bus daemon."""
        if self._connected:
            return

        self._bus = await MessageBus(bus_type=self._bus_type).connect()

        # Get introspection and create proxy
        introspection = await self._bus.introspect(self.SERVICE, self.OBJECT_PATH)
        proxy = self._bus.get_proxy_object(self.SERVICE, self.OBJECT_PATH, introspection)
        self._interface = proxy.get_interface(self.INTERFACE)
        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from the D-Bus daemon."""
        if self._bus:
            self._bus.disconnect()
            self._bus = None
            self._interface = None
            self._connected = False

    async def _ensure_connected(self) -> None:
        """Ensure we're connected to the daemon."""
        if not self._connected:
            await self.connect()

    # -------------------------------------------------------------------------
    # Signal Subscription Helpers
    # -------------------------------------------------------------------------

    async def run_operation_with_progress(
        self,
        operation_coro,
        timeout: float = 300.0,
    ) -> bool:
        """Run an operation and display progress via signals.

        This subscribes to D-Bus signals before starting the operation,
        displays progress messages, and returns when the operation completes.

        Args:
            operation_coro: Coroutine that calls a daemon method (returns op_id)
            timeout: Maximum time to wait for operation completion

        Returns:
            True if operation succeeded, False otherwise
        """
        await self._ensure_connected()

        # Track operation state
        my_operation_id: str | None = None
        done_event = asyncio.Event()
        success = False
        error_message = ""
        indent_stack: dict[str, int] = {}  # op_id -> current base indent

        def on_operation_started(args):
            nonlocal my_operation_id
            op_id, op_type, description, target = args
            if my_operation_id is not None and op_id != my_operation_id:
                return
            # Print the operation header
            out.section(description)
            indent_stack[op_id] = 0

        def on_operation_message(args):
            op_id, msg_type, message, indent = args
            if my_operation_id is not None and op_id != my_operation_id:
                return

            # Calculate total indent (operation base + message indent)
            base_indent = indent_stack.get(op_id, 0)
            total_indent = (base_indent + indent) * 2  # 2 spaces per level

            # Temporarily adjust output indent
            old_indent = out._indent_level
            out._indent_level = total_indent

            try:
                if msg_type == MessageType.INFO:
                    out.info(message)
                elif msg_type == MessageType.SUCCESS:
                    out.success(message)
                elif msg_type == MessageType.WARNING:
                    out.warning(message)
                elif msg_type == MessageType.ERROR:
                    out.error(message)
                elif msg_type == MessageType.DIM:
                    out.dim(message)
                elif msg_type == MessageType.HINT:
                    out.hint(message)
            finally:
                out._indent_level = old_indent

        def on_operation_completed(args):
            nonlocal success, error_message
            op_id, op_success, message = args
            if my_operation_id is not None and op_id != my_operation_id:
                return

            success = op_success
            error_message = message

            # Print final message if there is one
            if message:
                if op_success:
                    out.success(message)
                else:
                    out.error(message)

            done_event.set()

        # Subscribe to signals
        self._interface.on_operation_started(on_operation_started)
        self._interface.on_operation_message(on_operation_message)
        self._interface.on_operation_completed(on_operation_completed)

        try:
            # Start the operation
            my_operation_id = await operation_coro

            # Wait for completion with timeout
            try:
                await asyncio.wait_for(done_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                out.error(f"Operation timed out after {timeout} seconds")
                return False

            return success

        finally:
            # Unsubscribe from signals
            self._interface.off_operation_started(on_operation_started)
            self._interface.off_operation_message(on_operation_message)
            self._interface.off_operation_completed(on_operation_completed)

    # -------------------------------------------------------------------------
    # Daemon Methods - Container Lifecycle
    # -------------------------------------------------------------------------

    async def create_container(
        self,
        name: str,
        image: str,
        session_mode: bool = False,
        dbus_mux: bool = False,
    ) -> bool:
        """Create a new container.

        Args:
            name: Container name
            image: Image to use (e.g., "images:archlinux")
            session_mode: Enable session mode
            dbus_mux: Enable D-Bus multiplexer

        Returns:
            True if successful
        """
        await self._ensure_connected()

        async def call():
            return await self._interface.call_create_container(
                name, image, session_mode, dbus_mux
            )

        return await self.run_operation_with_progress(call())

    async def delete_container(self, name: str, force: bool = False) -> bool:
        """Delete a container.

        Args:
            name: Container name
            force: Force removal even if running

        Returns:
            True if successful
        """
        await self._ensure_connected()

        async def call():
            return await self._interface.call_delete_container(name, force)

        return await self.run_operation_with_progress(call())

    async def start_container(self, name: str) -> bool:
        """Start a container.

        Args:
            name: Container name

        Returns:
            True if successful
        """
        await self._ensure_connected()

        async def call():
            return await self._interface.call_start_container(name)

        return await self.run_operation_with_progress(call())

    async def stop_container(self, name: str, force: bool = False) -> bool:
        """Stop a container.

        Args:
            name: Container name
            force: Force stop

        Returns:
            True if successful
        """
        await self._ensure_connected()

        async def call():
            return await self._interface.call_stop_container(name, force)

        return await self.run_operation_with_progress(call())

    async def setup_user(
        self,
        container_name: str,
        uid: int,
        gid: int,
        username: str,
        home_dir: str,
    ) -> bool:
        """Set up a user in a container.

        Args:
            container_name: Container name
            uid: User ID
            gid: Group ID
            username: Username
            home_dir: Home directory path

        Returns:
            True if successful
        """
        await self._ensure_connected()

        async def call():
            return await self._interface.call_setup_user(
                container_name, uid, gid, username, home_dir
            )

        return await self.run_operation_with_progress(call())

    # -------------------------------------------------------------------------
    # Daemon Methods - Queries (immediate response, no signals)
    # -------------------------------------------------------------------------

    async def list_containers(self) -> list[tuple[str, str, str, str, str]]:
        """List all containers.

        Returns:
            List of (name, status, image, created, mode) tuples
        """
        await self._ensure_connected()
        return await self._interface.call_list_containers()

    async def get_container_info(self, name: str) -> dict[str, str]:
        """Get container information.

        Args:
            name: Container name

        Returns:
            Dictionary with container details
        """
        await self._ensure_connected()
        return await self._interface.call_get_container_info(name)

    async def is_user_setup(self, container_name: str, uid: int) -> bool:
        """Check if a user is set up in a container.

        Args:
            container_name: Container name
            uid: User ID

        Returns:
            True if user is set up
        """
        await self._ensure_connected()
        return await self._interface.call_is_user_setup(container_name, uid)

    @property
    async def version(self) -> str:
        """Get daemon version."""
        await self._ensure_connected()
        return await self._interface.get_version()


# Module-level singleton
_client: DaemonClient | None = None


def get_daemon_client() -> DaemonClient:
    """Get the shared DaemonClient instance."""
    global _client
    if _client is None:
        _client = DaemonClient()
    return _client
