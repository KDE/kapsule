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
import contextlib
import logging
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from .incus_client import IncusClient

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _log_on_failure(msg: str, *args: object) -> AsyncGenerator[None]:
    """Suppress any exception, logging it as a warning."""
    try:
        yield
    except Exception:
        logger.warning(msg, *args, exc_info=True)


# D-Bus service definitions for the three host-config sources.
_TIMEDATE_BUS = "org.freedesktop.timedate1"
_TIMEDATE_PATH = "/org/freedesktop/timedate1"

_LOCALE_BUS = "org.freedesktop.locale1"
_LOCALE_PATH = "/org/freedesktop/locale1"

_RESOLVE_BUS = "org.freedesktop.resolve1"
_RESOLVE_PATH = "/org/freedesktop/resolve1"

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
        await self._subscribe_timedate()
        await self._subscribe_locale()
        await self._subscribe_resolve()

    async def sync_container(self, container_name: str) -> None:
        """Push all current host config values into a single container.

        Intended to be called at container creation time so the new
        container starts with the host's current timezone, locale, and
        DNS configuration.
        """
        await self._sync_timezone_to(container_name)
        await self._sync_locale_to(container_name)
        await self._sync_dns_to(container_name)

    # ------------------------------------------------------------------
    # Subscription helpers
    # ------------------------------------------------------------------

    async def _subscribe_timedate(self) -> None:
        async with _log_on_failure(
            "Could not subscribe to %s — timezone sync disabled", _TIMEDATE_BUS
        ):
            await _subscribe_properties_changed(
                self._bus,
                _TIMEDATE_BUS,
                _TIMEDATE_PATH,
                self._on_timedate_changed,
            )
            logger.info("Subscribed to timezone changes on %s", _TIMEDATE_BUS)

    async def _subscribe_locale(self) -> None:
        async with _log_on_failure(
            "Could not subscribe to %s — locale sync disabled", _LOCALE_BUS
        ):
            await _subscribe_properties_changed(
                self._bus,
                _LOCALE_BUS,
                _LOCALE_PATH,
                self._on_locale_changed,
            )
            logger.info("Subscribed to locale changes on %s", _LOCALE_BUS)

    async def _subscribe_resolve(self) -> None:
        async with _log_on_failure(
            "Could not subscribe to %s — DNS sync disabled", _RESOLVE_BUS
        ):
            await _subscribe_properties_changed(
                self._bus,
                _RESOLVE_BUS,
                _RESOLVE_PATH,
                self._on_resolve_changed,
            )
            logger.info("Subscribed to DNS changes on %s", _RESOLVE_BUS)

    # ------------------------------------------------------------------
    # Signal callbacks
    # ------------------------------------------------------------------

    def _on_timedate_changed(self, msg: Message) -> bool | None:
        if (
            msg.message_type != MessageType.SIGNAL
            or msg.member != "PropertiesChanged"
            or msg.path != _TIMEDATE_PATH
        ):
            return None
        changed: dict[str, Variant] = msg.body[1]
        if "Timezone" not in changed:
            return None
        tz_value: str = changed["Timezone"].value
        logger.info("Host timezone changed to %s", tz_value)
        asyncio.ensure_future(self._sync_running_containers("timezone", tz_value))
        return None

    def _on_locale_changed(self, msg: Message) -> bool | None:
        if (
            msg.message_type != MessageType.SIGNAL
            or msg.member != "PropertiesChanged"
            or msg.path != _LOCALE_PATH
        ):
            return None
        changed: dict[str, Variant] = msg.body[1]
        if "Locale" not in changed:
            return None
        locale_array: list[str] = changed["Locale"].value
        joined = "\n".join(locale_array)
        logger.info("Host locale changed: %s", locale_array)
        asyncio.ensure_future(self._sync_running_containers("locale", joined))
        return None

    def _on_resolve_changed(self, msg: Message) -> bool | None:
        if (
            msg.message_type != MessageType.SIGNAL
            or msg.member != "PropertiesChanged"
            or msg.path != _RESOLVE_PATH
        ):
            return None
        try:
            resolv_content = Path("/etc/resolv.conf").read_text()
        except OSError:
            logger.warning("Could not read /etc/resolv.conf after DNS change")
            return None
        logger.info("Host DNS configuration changed")
        asyncio.ensure_future(self._sync_running_containers("dns", resolv_content))
        return None

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
            async with _log_on_failure(
                "Failed to sync %s into container %s", sync_type, container.name
            ):
                await self._exec_sync_script(container.name, sync_type, data)

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

    # ------------------------------------------------------------------
    # Single-container sync (used at creation time)
    # ------------------------------------------------------------------

    async def _sync_timezone_to(self, container_name: str) -> None:
        async with _log_on_failure(
            "Could not sync timezone into container %s", container_name
        ):
            variant = await _get_dbus_property(
                self._bus, _TIMEDATE_BUS, _TIMEDATE_PATH, _TIMEDATE_BUS, "Timezone"
            )
            timezone: str = variant.value
            await self._exec_sync_script(container_name, "timezone", timezone)

    async def _sync_locale_to(self, container_name: str) -> None:
        async with _log_on_failure(
            "Could not sync locale into container %s", container_name
        ):
            variant = await _get_dbus_property(
                self._bus, _LOCALE_BUS, _LOCALE_PATH, _LOCALE_BUS, "Locale"
            )
            locale_array: list[str] = variant.value
            joined = "\n".join(locale_array)
            await self._exec_sync_script(container_name, "locale", joined)

    async def _sync_dns_to(self, container_name: str) -> None:
        async with _log_on_failure(
            "Could not sync DNS into container %s", container_name
        ):
            resolv_content = Path("/etc/resolv.conf").read_text()
            await self._exec_sync_script(container_name, "dns", resolv_content)
