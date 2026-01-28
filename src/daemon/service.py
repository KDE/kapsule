"""D-Bus service implementation for Kapsule.

Uses dbus-fast for async D-Bus communication.
"""

from __future__ import annotations

from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method, dbus_property, signal, PropertyAccess
from dbus_fast import BusType

from . import __version__
from .incus_client import IncusClient


class KapsuleManagerInterface(ServiceInterface):
    """org.kde.kapsule.Manager D-Bus interface.

    Provides container management operations over D-Bus.
    """

    def __init__(self, incus: IncusClient):
        super().__init__("org.kde.kapsule.Manager")
        self._incus = incus
        self._version = __version__

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @dbus_property(access=PropertyAccess.READ)
    def Version(self) -> "s":
        """Daemon version."""
        return self._version

    # -------------------------------------------------------------------------
    # Methods
    # -------------------------------------------------------------------------

    @method()
    async def IsIncusAvailable(self) -> "b":
        """Check whether Incus is available."""
        return await self._incus.is_available()

    @method()
    async def ListContainers(self) -> "a(ssss)":
        """List all containers.

        Returns:
            Array of structs: (name, status, image, created)
        """
        containers = await self._incus.list_containers()
        return [c.to_dbus_struct() for c in containers]

    @method()
    async def GetShellCommand(self, name: "s") -> "as":
        """Get command to enter a container shell.

        Args:
            name: Container name.

        Returns:
            Command as array of strings.
        """
        # TODO: Get actual user from container or use current user
        return ["incus", "exec", name, "--", "bash", "-l"]

    # -------------------------------------------------------------------------
    # Signals (for future progress reporting)
    # -------------------------------------------------------------------------

    @signal()
    def ContainerStateChanged(self, name: str, state: str) -> "ss":
        """Emitted when a container's state changes."""
        return [name, state]

    @signal()
    def OperationProgress(
        self,
        operation_id: str,
        stage: str,
        progress: float,
        message: str,
    ) -> "ssds":
        """Emitted during long-running operations.

        Args:
            operation_id: Unique ID for the operation.
            stage: Current stage (e.g., "download", "create", "start").
            progress: Progress 0.0-1.0, or -1 for indeterminate.
            message: Human-readable status message.
        """
        return [operation_id, stage, progress, message]


class KapsuleService:
    """Main D-Bus service manager."""

    def __init__(self, bus_type: str = "session"):
        """Initialize the service.

        Args:
            bus_type: "session" or "system" bus.
        """
        self._bus_type = BusType.SYSTEM if bus_type == "system" else BusType.SESSION
        self._bus: MessageBus | None = None
        self._incus = IncusClient()
        self._interface: KapsuleManagerInterface | None = None

    async def start(self) -> None:
        """Start the D-Bus service."""
        self._bus = await MessageBus(bus_type=self._bus_type).connect()

        self._interface = KapsuleManagerInterface(self._incus)

        # Export the interface at /org/kde/kapsule
        self._bus.export("/org/kde/kapsule", self._interface)

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
        if self._bus:
            self._bus.disconnect()
            self._bus = None
