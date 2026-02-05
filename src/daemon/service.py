# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""D-Bus service implementation for Kapsule.

Provides the org.kde.kapsule.Manager interface for container management.
"""

from __future__ import annotations

import contextvars
from typing import Annotated

from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_property, dbus_method, dbus_signal
from dbus_fast.constants import PropertyAccess
from dbus_fast.annotations import DBusStr, DBusBool, DBusObjectPath, DBusSignature, DBusUInt32
from dbus_fast import BusType, Message, MessageType

from .dbus_types import (
    DBusStrArray,
    DBusStrDict,
    DBusContainer,
    DBusContainerList,
    DBusEnterResult,
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

    @classmethod
    def create_deferred(cls, bus: MessageBus) -> "KapsuleManagerInterface":
        """Create an interface with deferred service initialization.

        Used when the container service needs a reference to this interface.
        Call set_service() after creating the ContainerService.
        """
        # Create instance without calling __init__
        instance = cls.__new__(cls)
        ServiceInterface.__init__(instance, "org.kde.kapsule.Manager")
        instance._version = __version__
        instance._bus = bus
        instance._service = None  # type: ignore[assignment]
        return instance

    def set_service(self, service: ContainerService) -> None:
        """Set the container service (for deferred initialization)."""
        self._service = service

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
        env: dict[str, str] = {}
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
    # Signals for Operation Lifecycle (for monitoring tools)
    # =========================================================================
    # Note: Detailed progress signals are emitted on individual operation
    # objects at /org/kde/kapsule/operations/{id}. These global signals
    # are for tools that want to monitor all operations without subscribing
    # to each one.

    @dbus_signal()
    def OperationCreated(
        self,
        object_path: DBusObjectPath,
        operation_type: DBusStr,
        target: DBusStr,
    ) -> Annotated[tuple[str, str, str], DBusSignature("oss")]:
        """Emitted when a new operation is created.

        Clients can use the object_path to subscribe to the operation's
        signals for detailed progress.

        Args:
            object_path: D-Bus path to the operation object
            operation_type: Type of operation (create, delete, start, stop, setup_user)
            target: Target of the operation (usually container name)
        """
        return (object_path, operation_type, target)

    @dbus_signal()
    def OperationRemoved(
        self,
        object_path: DBusObjectPath,
        success: DBusBool,
    ) -> Annotated[tuple[str, bool], DBusSignature("ob")]:
        """Emitted when an operation is removed (after completion + cleanup delay).

        Args:
            object_path: D-Bus path to the operation that was removed
            success: Whether the operation succeeded
        """
        return (object_path, success)

    # =========================================================================
    # Methods - Operations Query
    # =========================================================================

    @dbus_method()
    def ListOperations(self) -> Annotated[list[str], DBusSignature("ao")]:
        """List all currently running operations.

        Returns:
            Array of D-Bus object paths for running operations
        """
        return self._service.list_operations()

    # =========================================================================
    # Methods - Container Lifecycle
    # =========================================================================

    @dbus_method()
    async def CreateContainer(
        self,
        name: DBusStr,
        image: DBusStr,
        session_mode: DBusBool,
        dbus_mux: DBusBool,
    ) -> DBusObjectPath:
        """Create a new container.

        Args:
            name: Container name
            image: Image to use (e.g., "archlinux"), empty for default from config
            session_mode: Enable session mode with container D-Bus
            dbus_mux: Enable D-Bus multiplexer (implies session_mode)

        Returns:
            D-Bus object path for tracking operation progress
        """
        # If no image specified, look up default from caller's config
        actual_image = image
        if not image:
            sender = _current_sender.get()
            if not sender:
                raise Exception("No image specified and could not determine caller identity")

            try:
                uid, _gid, _pid = await self._get_caller_credentials(sender)
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

    @dbus_method()
    async def DeleteContainer(self, name: DBusStr, force: DBusBool) -> DBusObjectPath:
        """Delete a container.

        Args:
            name: Container name
            force: Force removal even if running

        Returns:
            D-Bus object path for tracking operation progress
        """
        return await self._service.delete_container(name=name, force=force)

    @dbus_method()
    async def StartContainer(self, name: DBusStr) -> DBusObjectPath:
        """Start a stopped container.

        Args:
            name: Container name

        Returns:
            D-Bus object path for tracking operation progress
        """
        return await self._service.start_container(name=name)

    @dbus_method()
    async def StopContainer(self, name: DBusStr, force: DBusBool) -> DBusObjectPath:
        """Stop a running container.

        Args:
            name: Container name
            force: Force stop

        Returns:
            D-Bus object path for tracking operation progress
        """
        return await self._service.stop_container(name=name, force=force)

    # =========================================================================
    # Methods - User Setup
    # =========================================================================

    @dbus_method()
    async def SetupUser(
        self,
        container_name: DBusStr,
        uid: DBusUInt32,
        gid: DBusUInt32,
        username: DBusStr,
        home_dir: DBusStr,
    ) -> DBusObjectPath:
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
            D-Bus object path for tracking operation progress
        """
        return await self._service.setup_user(
            container_name=container_name,
            uid=uid,
            gid=gid,
            username=username,
            home_dir=home_dir,
        )

    @dbus_method()
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

    @dbus_method()
    async def ListContainers(self) -> DBusContainerList:
        """List all containers.

        Returns:
            Array of (name, status, image, created, mode) tuples
        """
        return await self._service.list_containers()

    @dbus_method()
    async def GetContainerInfo(self, name: DBusStr) -> DBusContainer:
        """Get information about a container.

        Args:
            name: Container name

        Returns:
            Tuple of (name, status, image, created, mode)
        """
        return await self._service.get_container_info(name)

    @dbus_method()
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
            uid, _gid, _pid = await self._get_caller_credentials(sender)
        except RuntimeError as e:
            return {"error": f"Failed to get caller credentials: {e}"}

        return await self._service.get_config(uid)

    # =========================================================================
    # Methods - Enter Container
    # =========================================================================

    @dbus_method()
    async def PrepareEnter(
        self,
        container_name: DBusStr,
        command: DBusStrArray,
    ) -> DBusEnterResult:
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
        # So we use deferred initialization
        temp_interface = KapsuleManagerInterface.create_deferred(self._bus)

        self._container_service = ContainerService(temp_interface, self._incus)
        self._container_service.set_bus(self._bus)  # Enable operation D-Bus objects
        temp_interface.set_service(self._container_service)

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
