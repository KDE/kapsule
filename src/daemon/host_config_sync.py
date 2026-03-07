# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sync host timezone, locale, and DNS configuration into running containers.

This module subscribes to systemd D-Bus ``PropertiesChanged`` signals on
``timedate1``, ``locale1``, and ``resolve1`` to detect changes to the
host's timezone, locale, and DNS resolver configuration.  When a change
is detected the corresponding ``/.kapsule/sync/<name>`` script is
executed inside every running Kapsule container with the new value on
stdin.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from .incus_client import IncusClient

logger = logging.getLogger(__name__)

_PROPS_INTERFACE = "org.freedesktop.DBus.Properties"


# ------------------------------------------------------------------
# Typed D-Bus helpers (avoid dbus-fast's untyped dynamic proxies)
# ------------------------------------------------------------------


async def _get_dbus_property(
    bus: MessageBus,
    service: str,
    path: str,
    interface: str,
    property_name: str,
) -> Variant:
    """Read a single D-Bus property via org.freedesktop.DBus.Properties.Get.

    Returns the raw ``Variant`` so callers can extract ``.value`` with
    an explicit type annotation at the call site.
    """
    reply = await bus.call(
        Message(
            destination=service,
            path=path,
            interface=_PROPS_INTERFACE,
            member="Get",
            signature="ss",
            body=[interface, property_name],
        )
    )
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(
            f"D-Bus Properties.Get({interface}, {property_name}) failed: {reply.body}"
        )
    result: Variant = reply.body[0]
    return result


async def _subscribe_properties_changed(
    bus: MessageBus,
    service: str,
    path: str,
    handler: Callable[[Message], bool | None],
) -> None:
    """Subscribe to ``PropertiesChanged`` signals for a specific service.

    Sends an ``AddMatch`` rule to the bus daemon and registers *handler*
    as a message handler.  The handler receives every message on the bus
    and should return ``None`` for messages it does not handle.

    The ``PropertiesChanged`` signal body (signature ``sa{sv}as``) is:

    * ``body[0]``: interface name (``str``)
    * ``body[1]``: changed properties (``dict[str, Variant]``)
    * ``body[2]``: invalidated property names (``list[str]``)
    """
    match_rule = (
        "type='signal',"
        f"sender='{service}',"
        f"path='{path}',"
        f"interface='{_PROPS_INTERFACE}',"
        "member='PropertiesChanged'"
    )
    await bus.call(
        Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus",
            member="AddMatch",
            signature="s",
            body=[match_rule],
        )
    )
    bus.add_message_handler(handler)


# ------------------------------------------------------------------
# Sync source descriptors
# ------------------------------------------------------------------


@dataclass(frozen=True)
class _SyncSource:
    """Declarative description of a single host-config sync source.

    Each instance captures the D-Bus coordinates, a property to watch
    for changes, how to extract the sync payload from a
    ``PropertiesChanged`` signal, and how to fetch the current value
    for initial container sync.

    Attributes:
        name: Human-readable label and sync-script name (e.g. ``"timezone"``).
        bus_name: D-Bus service name (e.g. ``"org.freedesktop.timedate1"``).
        object_path: D-Bus object path.
        watched_property: If set, the handler only fires when this property
            appears in the ``PropertiesChanged`` dict.  If ``None``, any
            property change on the object triggers the handler.
        extract_from_signal: Synchronous callable that receives the
            ``changed`` dict (``dict[str, Variant]``) from the signal
            and returns the data string to pipe into containers.
        fetch_current: Async callable that receives the ``MessageBus``
            and returns the current data string (used for initial sync
            at container creation time).
    """

    name: str
    bus_name: str
    object_path: str
    watched_property: str | None
    extract_from_signal: Callable[[dict[str, Variant]], str]
    fetch_current: Callable[[MessageBus], Awaitable[str]]


# -- fetch_current helpers -------------------------------------------------


async def _fetch_timezone(bus: MessageBus) -> str:
    variant = await _get_dbus_property(
        bus,
        "org.freedesktop.timedate1",
        "/org/freedesktop/timedate1",
        "org.freedesktop.timedate1",
        "Timezone",
    )
    result: str = variant.value
    return result


async def _fetch_locale(bus: MessageBus) -> str:
    variant = await _get_dbus_property(
        bus,
        "org.freedesktop.locale1",
        "/org/freedesktop/locale1",
        "org.freedesktop.locale1",
        "Locale",
    )
    locale_array: list[str] = variant.value
    return "\n".join(locale_array)


async def _fetch_dns(_bus: MessageBus) -> str:
    return Path("/etc/resolv.conf").read_text()


_SYNC_SOURCES: list[_SyncSource] = [
    _SyncSource(
        name="timezone",
        bus_name="org.freedesktop.timedate1",
        object_path="/org/freedesktop/timedate1",
        watched_property="Timezone",
        extract_from_signal=lambda changed: changed["Timezone"].value,
        fetch_current=_fetch_timezone,
    ),
    _SyncSource(
        name="locale",
        bus_name="org.freedesktop.locale1",
        object_path="/org/freedesktop/locale1",
        watched_property="Locale",
        extract_from_signal=lambda changed: "\n".join(changed["Locale"].value),
        fetch_current=_fetch_locale,
    ),
    _SyncSource(
        name="dns",
        bus_name="org.freedesktop.resolve1",
        object_path="/org/freedesktop/resolve1",
        watched_property=None,
        extract_from_signal=lambda _: Path("/etc/resolv.conf").read_text(),
        fetch_current=_fetch_dns,
    ),
]


class HostConfigSync:
    """Watch host config via D-Bus and push changes into containers."""

    def __init__(self, bus: MessageBus, incus: IncusClient) -> None:
        self._bus = bus
        self._incus = incus

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to PropertiesChanged signals for host config sources.

        Each subscription is independent — if one service is unavailable
        (e.g. systemd-resolved is not running) a warning is logged and
        the remaining subscriptions proceed normally.
        """
        for source in _SYNC_SOURCES:
            await self._subscribe(source)

    async def sync_container(self, container_name: str) -> None:
        """Push all current host config values into a single container.

        Intended to be called at container creation time so the new
        container starts with the host's current timezone, locale, and
        DNS configuration.
        """
        for source in _SYNC_SOURCES:
            await self._sync_to(source, container_name)

    # ------------------------------------------------------------------
    # Generic subscription & signal handling
    # ------------------------------------------------------------------

    async def _subscribe(self, source: _SyncSource) -> None:
        """Subscribe to PropertiesChanged for a single sync source."""
        try:
            handler = self._make_handler(source)
            await _subscribe_properties_changed(
                self._bus, source.bus_name, source.object_path, handler
            )
            logger.info("Subscribed to %s changes on %s", source.name, source.bus_name)
        except Exception:
            logger.warning(
                "Could not subscribe to %s — %s sync disabled",
                source.bus_name,
                source.name,
                exc_info=True,
            )

    def _make_handler(self, source: _SyncSource) -> Callable[[Message], bool | None]:
        """Create a D-Bus message handler closure for *source*."""

        def handler(msg: Message) -> bool | None:
            if (
                msg.message_type != MessageType.SIGNAL
                or msg.member != "PropertiesChanged"
                or msg.path != source.object_path
            ):
                return None

            changed: dict[str, Variant] = msg.body[1]
            if source.watched_property and source.watched_property not in changed:
                return None

            try:
                data = source.extract_from_signal(changed)
            except Exception:
                logger.warning(
                    "Failed to extract %s data from signal",
                    source.name,
                    exc_info=True,
                )
                return None

            logger.info("Host %s changed", source.name)
            asyncio.ensure_future(self._sync_running_containers(source.name, data))
            return None

        return handler

    # ------------------------------------------------------------------
    # Single-container sync (used at creation time)
    # ------------------------------------------------------------------

    async def _sync_to(self, source: _SyncSource, container_name: str) -> None:
        """Fetch the current value for *source* and sync it into one container."""
        try:
            data = await source.fetch_current(self._bus)
            await self._exec_sync_script(container_name, source.name, data)
        except Exception:
            logger.warning(
                "Could not sync %s into container %s",
                source.name,
                container_name,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Container sync logic
    # ------------------------------------------------------------------

    async def _sync_running_containers(self, sync_type: str, data: str) -> None:
        """Execute the sync script in every running container."""
        try:
            containers = await self._incus.list_containers()
        except Exception:
            logger.warning(
                "Failed to list containers for %s sync", sync_type, exc_info=True
            )
            return

        for container in containers:
            if container.status != "Running":
                continue
            try:
                await self._exec_sync_script(container.name, sync_type, data)
            except Exception:
                logger.warning(
                    "Failed to sync %s into container %s",
                    sync_type,
                    container.name,
                    exc_info=True,
                )

    async def _exec_sync_script(self, name: str, sync_type: str, data: str) -> None:
        """Run the sync script in a single container if it exists."""
        script_path = f"/.kapsule/sync/{sync_type}"

        # Check whether the sync script exists and is executable.
        check = await asyncio.create_subprocess_exec(
            "incus",
            "exec",
            name,
            "--",
            "test",
            "-x",
            script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await check.communicate()
        if check.returncode != 0:
            return

        # Execute the script, passing the data on stdin.
        proc = await asyncio.create_subprocess_exec(
            "incus",
            "exec",
            name,
            "--",
            script_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await proc.communicate(input=data.encode())
        if proc.returncode != 0:
            logger.warning(
                "Sync script %s failed in container %s (rc=%d): %s",
                script_path,
                name,
                proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
        else:
            logger.info("Synced %s into container %s", sync_type, name)
