"""D-Bus service implementation for Kapsule.

Provides the org.kde.kapsule.Manager interface for container management.
"""

from __future__ import annotations

import asyncio
import contextvars
import grp
from typing import Annotated

from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface
from dbus_fast import BusType, Variant, Message, MessageType

from .dbus_types import (
    DBusStr,
    DBusBool,
    DBusUInt32,
    DBusStrArray,
    DBusStrDict,
    dbus_property,
    method,
    signal,
    PropertyAccess,
)

from . import __version__
from .container_service import ContainerService

# Re-export IncusClient for use in __main__ and CLI
from .incus_client import IncusClient

# Context variable to store the current D-Bus message sender
# This is set by a message handler before method dispatch
_current_sender: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_sender", default=None
)


class KapsuleManagerInterface(ServiceInterface):
    """org.kde.kapsule.Manager D-Bus interface.

    This interface provides:
    - Container lifecycle management (create, delete, start, stop)
    - User setup in containers
    - Progress reporting via signals

    All long-running operations return an operation ID immediately.
    Progress is reported via signals that clients can subscribe to.
    """

    def __init__(self, container_service: ContainerService, bus: MessageBus | None = None):
        super().__init__("org.kde.kapsule.Manager")
        self._service = container_service
        self._version = __version__
        self._bus = bus

    def set_bus(self, bus: MessageBus) -> None:
        """Set the message bus for credential lookups."""
        self._bus = bus

    async def _get_caller_credentials(self, sender: str) -> tuple[int, int, int]:
        """Get the UID, GID, and PID of a D-Bus caller.

        Args:
            sender: The unique bus name of the caller (e.g., ":1.123")

        Returns:
            Tuple of (uid, gid, pid)

        Raises:
            RuntimeError: If credentials cannot be obtained
        """
        if self._bus is None:
            raise RuntimeError("Bus not set")

        # Get UID
        msg = Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="GetConnectionUnixUser",
            signature="s",
            body=[sender],
        )
        reply = await self._bus.call(msg)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"Failed to get UID: {reply.body[0] if reply.body else 'unknown error'}")
        uid = reply.body[0]

        # Get PID to look up GID and environment
        msg = Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="GetConnectionUnixProcessID",
            signature="s",
            body=[sender],
        )
        reply = await self._bus.call(msg)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"Failed to get PID: {reply.body[0] if reply.body else 'unknown error'}")
        pid = reply.body[0]

        # Read GID from /proc/<pid>/status
        try:
            with open(f"/proc/{pid}/status", "r") as f:
                for line in f:
                    if line.startswith("Gid:"):
                        # Format: "Gid:\treal\teffective\tsaved\tfs"
                        gid = int(line.split()[1])
                        break
                else:
                    gid = uid  # Fallback to UID if GID not found
        except (FileNotFoundError, PermissionError, ValueError):
            gid = uid  # Fallback

        return uid, gid, pid

    def _get_process_environ(self, pid: int) -> dict[str, str]:
        """Read environment variables from a process.

        Args:
            pid: Process ID to read environment from

        Returns:
            Dictionary of environment variables
        """
        env = {}
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                data = f.read()
                # environ is null-separated key=value pairs
                for item in data.split(b"\x00"):
                    if b"=" in item:
                        key, _, value = item.partition(b"=")
                        try:
                            env[key.decode("utf-8")] = value.decode("utf-8")
                        except UnicodeDecodeError:
                            pass  # Skip non-UTF-8 entries
        except (FileNotFoundError, PermissionError):
            pass  # Return empty dict on error
        return env

    # =========================================================================
    # Properties
    # =========================================================================

    @dbus_property(access=PropertyAccess.READ)
    def Version(self) -> DBusStr:
        """Daemon version."""
        return self._version

    # =========================================================================
    # Signals for Operation Progress
    # =========================================================================

    @signal()
    def OperationStarted(
        self,
        operation_id: DBusStr,
        operation_type: DBusStr,
        description: DBusStr,
        target: DBusStr,
    ) -> Annotated[tuple[str, str, str, str], "(ssss)"]:
        """Emitted when an operation begins.

        Args:
            operation_id: Unique ID for tracking this operation
            operation_type: Type of operation (create, delete, start, stop, setup_user)
            description: Human-readable description (e.g., "Creating container: mybox")
            target: Target of the operation (usually container name)
        """
        return (operation_id, operation_type, description, target)

    @signal()
    def OperationMessage(
        self,
        operation_id: DBusStr,
        message_type: int,
        message: DBusStr,
        indent_level: int,
    ) -> Annotated[tuple[str, int, str, int], "(sisi)"]:
        """Emitted for progress messages within an operation.

        Args:
            operation_id: Operation this message belongs to
            message_type: Type of message (0=info, 1=success, 2=warning, 3=error, 4=dim, 5=hint)
            message: The message text
            indent_level: Indentation level for hierarchical display
        """
        return (operation_id, message_type, message, indent_level)

    @signal()
    def OperationCompleted(
        self,
        operation_id: DBusStr,
        success: DBusBool,
        message: DBusStr,
    ) -> Annotated[tuple[str, bool, str], "(sbs)"]:
        """Emitted when an operation finishes.

        Args:
            operation_id: Operation that completed
            success: Whether the operation succeeded
            message: Final message (error message if failed)
        """
        return (operation_id, success, message)

    @signal()
    def ProgressStarted(
        self,
        operation_id: DBusStr,
        progress_id: DBusStr,
        description: DBusStr,
        total: int,
        indent_level: int,
    ) -> Annotated[tuple[str, str, str, int, int], "(sssii)"]:
        """Emitted when a progress bar starts.

        Args:
            operation_id: Parent operation
            progress_id: Unique ID for this progress bar
            description: What's being tracked (e.g., "Downloading image...")
            total: Total units (-1 for indeterminate)
            indent_level: Indentation level
        """
        return (operation_id, progress_id, description, total, indent_level)

    @signal()
    def ProgressUpdate(
        self,
        progress_id: DBusStr,
        current: int,
        rate: float,
    ) -> Annotated[tuple[str, int, float], "(sid)"]:
        """Emitted to update a progress bar.

        Args:
            progress_id: Progress bar to update
            current: Current progress value
            rate: Rate of progress (for ETA calculation)
        """
        return (progress_id, current, rate)

    @signal()
    def ProgressCompleted(
        self,
        progress_id: DBusStr,
        success: DBusBool,
        message: DBusStr,
    ) -> Annotated[tuple[str, bool, str], "(sbs)"]:
        """Emitted when a progress bar completes.

        Args:
            progress_id: Progress bar that completed
            success: Whether it succeeded
            message: Optional completion message (replaces bar)
        """
        return (progress_id, success, message)

    # =========================================================================
    # Methods - Container Lifecycle
    # =========================================================================

    @method()
    async def CreateContainer(
        self,
        name: DBusStr,
        image: DBusStr,
        session_mode: DBusBool,
        dbus_mux: DBusBool,
    ) -> DBusStr:
        """Create a new container.

        Args:
            name: Container name
            image: Image to use (e.g., "archlinux"), empty for default from config
            session_mode: Enable session mode with container D-Bus
            dbus_mux: Enable D-Bus multiplexer (implies session_mode)

        Returns:
            Operation ID for tracking progress, or error message if image not specified
        """
        # If no image specified, look up default from caller's config
        actual_image = image
        if not image:
            sender = _current_sender.get()
            if not sender:
                raise Exception("No image specified and could not determine caller identity")

            try:
                uid, gid, pid = await self._get_caller_credentials(sender)
                config = await self._service.get_config(uid)
                actual_image = config.get("default_image", "")
                if not actual_image:
                    raise Exception("No image specified and no default_image in config")
            except RuntimeError as e:
                raise Exception(f"No image specified and failed to read config: {e}")

        return await self._service.create_container(
            name=name,
            image=actual_image,
            session_mode=session_mode,
            dbus_mux=dbus_mux,
        )

    @method()
    async def DeleteContainer(self, name: DBusStr, force: DBusBool) -> DBusStr:
        """Delete a container.

        Args:
            name: Container name
            force: Force removal even if running

        Returns:
            Operation ID for tracking progress
        """
        return await self._service.delete_container(name=name, force=force)

    @method()
    async def StartContainer(self, name: DBusStr) -> DBusStr:
        """Start a stopped container.

        Args:
            name: Container name

        Returns:
            Operation ID for tracking progress
        """
        return await self._service.start_container(name=name)

    @method()
    async def StopContainer(self, name: DBusStr, force: DBusBool) -> DBusStr:
        """Stop a running container.

        Args:
            name: Container name
            force: Force stop

        Returns:
            Operation ID for tracking progress
        """
        return await self._service.stop_container(name=name, force=force)

    # =========================================================================
    # Methods - User Setup
    # =========================================================================

    @method()
    async def SetupUser(
        self,
        container_name: DBusStr,
        uid: DBusUInt32,
        gid: DBusUInt32,
        username: DBusStr,
        home_dir: DBusStr,
    ) -> DBusStr:
        """Set up a host user in a container.

        This mounts the user's home directory and creates a matching
        user account with passwordless sudo.

        Args:
            container_name: Container name
            uid: User ID
            gid: Group ID
            username: Username
            home_dir: Path to home directory on host

        Returns:
            Operation ID for tracking progress
        """
        return await self._service.setup_user(
            container_name=container_name,
            uid=uid,
            gid=gid,
            username=username,
            home_dir=home_dir,
        )

    @method()
    async def IsUserSetup(self, container_name: DBusStr, uid: DBusUInt32) -> DBusBool:
        """Check if a user is set up in a container.

        Args:
            container_name: Container name
            uid: User ID to check

        Returns:
            True if user is set up
        """
        return await self._service.is_user_setup(container_name, uid)

    # =========================================================================
    # Methods - Query
    # =========================================================================

    @method()
    async def ListContainers(self) -> Annotated[list[tuple[str, str, str, str, str]], "a(sssss)"]:
        """List all containers.

        Returns:
            Array of (name, status, image, created, mode) tuples
        """
        return await self._service.list_containers()

    @method()
    async def GetContainerInfo(self, name: DBusStr) -> DBusStrDict:
        """Get detailed information about a container.

        Args:
            name: Container name

        Returns:
            Dictionary with container details
        """
        return await self._service.get_container_info(name)

    @method()
    async def GetConfig(self) -> DBusStrDict:
        """Get user configuration.

        Reads configuration using the caller's home directory for
        user-specific config paths.

        Returns:
            Dictionary with config keys and values
        """
        # Get the sender from context (set by message handler)
        sender = _current_sender.get()
        if sender is None:
            return {"error": "Could not determine caller identity"}

        try:
            uid, gid, pid = await self._get_caller_credentials(sender)
        except RuntimeError as e:
            return {"error": f"Failed to get caller credentials: {e}"}

        return await self._service.get_config(uid)

    # =========================================================================
    # Methods - Enter Container
    # =========================================================================

    @method()
    async def PrepareEnter(
        self,
        container_name: DBusStr,
        command: DBusStrArray,
    ) -> Annotated[tuple[bool, str, list[str]], "(bsas)"]:
        """Prepare to enter a container.

        This method handles all setup for entering a container:
        - Creates the default container if needed
        - Starts the container if stopped
        - Sets up the calling user if needed
        - Configures runtime directory symlinks

        The caller's UID/GID and environment are obtained from D-Bus
        connection credentials and /proc.

        Args:
            container_name: Container to enter (empty string for default)
            command: Command to run inside (empty array for shell)

        Returns:
            Tuple of (success, error_message, command_array)
            On success: (True, "", ["incus", "exec", ...])
            On failure: (False, "error message", [])
        """
        # Get the sender from context (set by message handler)
        sender = _current_sender.get()
        if sender is None:
            return (False, "Could not determine caller identity", [])

        try:
            uid, gid, pid = await self._get_caller_credentials(sender)
        except RuntimeError as e:
            return (False, f"Failed to get caller credentials: {e}", [])

        # Read environment from caller's process
        env = self._get_process_environ(pid)

        success, message, cmd = await self._service.prepare_enter(
            uid=uid,
            gid=gid,
            container_name=container_name if container_name else None,
            command=list(command),
            env=env,
        )
        return (success, message, cmd)


class KapsuleService:
    """Main D-Bus service manager.

    Handles D-Bus connection lifecycle and hosts the KapsuleManagerInterface.
    """

    def __init__(
        self,
        bus_type: str = "system",
        socket_path: str = "/var/lib/incus/unix.socket",
    ):
        """Initialize the service.

        Args:
            bus_type: "session" or "system" bus for the daemon's interface
            socket_path: Path to Incus Unix socket
        """
        self._bus_type = BusType.SYSTEM if bus_type == "system" else BusType.SESSION
        self._socket_path = socket_path
        self._bus: MessageBus | None = None
        self._interface: KapsuleManagerInterface | None = None
        self._incus: IncusClient | None = None
        self._container_service: ContainerService | None = None

    async def start(self) -> None:
        """Start the D-Bus service."""
        # Connect to D-Bus
        self._bus = await MessageBus(bus_type=self._bus_type).connect()

        # Create Incus client
        self._incus = IncusClient(socket_path=self._socket_path)

        # Create the interface and container service
        # The interface needs the service, and the service needs the interface
        # So we create them in two steps using __new__
        temp_interface = KapsuleManagerInterface.__new__(KapsuleManagerInterface)
        ServiceInterface.__init__(temp_interface, "org.kde.kapsule.Manager")
        temp_interface._version = __version__
        temp_interface._bus = self._bus  # Set bus for credential lookups

        self._container_service = ContainerService(temp_interface, self._incus)
        temp_interface._service = self._container_service

        self._interface = temp_interface

        # Export the interface
        self._bus.export("/org/kde/kapsule", self._interface)

        # Add message handler to capture sender for credential verification
        def capture_sender(msg: Message) -> bool | None:
            """Capture the sender of incoming method calls."""
            if msg.message_type == MessageType.METHOD_CALL:
                _current_sender.set(msg.sender)
            return None  # Let normal processing continue

        self._bus.add_message_handler(capture_sender)

        # Request the well-known name
        await self._bus.request_name("org.kde.kapsule")

        bus_name = "system" if self._bus_type == BusType.SYSTEM else "session"
        print(f"Kapsule daemon v{__version__} running on {bus_name} bus")
        print("Service: org.kde.kapsule")
        print("Object:  /org/kde/kapsule")

    async def run(self) -> None:
        """Run the service until disconnected."""
        if self._bus is None:
            raise RuntimeError("Service not started")
        await self._bus.wait_for_disconnect()

    async def stop(self) -> None:
        """Stop the D-Bus service."""
        if self._incus:
            await self._incus.close()
            self._incus = None

        if self._bus:
            self._bus.disconnect()
            self._bus = None

    @property
    def container_service(self) -> ContainerService | None:
        """Get the container service."""
        return self._container_service
