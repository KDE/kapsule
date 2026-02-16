# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Creation pipeline step: configure session mode / rootless Podman."""

from __future__ import annotations

import os
import subprocess

from ...incus_client import IncusClient, IncusError
from ...operations import OperationError, OperationReporter
from ..constants import (
    KAPSULE_DBUS_MUX_BIN,
    KAPSULE_DBUS_SOCKET_SYSTEMD,
)
from ..contexts import CreateContext
from . import create_pipeline


@create_pipeline.step(order=300)
async def session_mode(ctx: CreateContext) -> None:
    """Set up session mode if enabled, otherwise configure rootless Podman."""
    if ctx.opts.session_mode:
        await _setup_session_mode_impl(
            ctx.name, ctx.opts.dbus_mux, ctx.incus, ctx.progress,
        )
    else:
        # Non-session containers lack a systemd user instance, so
        # rootless Podman's default cgroup_manager=systemd will fail.
        await _configure_rootless_podman_impl(ctx.name, ctx.incus, ctx.progress)


async def _setup_session_mode_impl(
    name: str,
    dbus_mux: bool,
    incus: IncusClient,
    progress: OperationReporter | None,
) -> None:
    """Set up session mode for a container.

    Without D-Bus mux, the container's own systemd dbus.socket creates
    /run/user/$uid/bus natively — no extra setup is needed (loginctl
    enable-linger is handled by the user setup pipeline).

    With D-Bus mux, we redirect the container's dbus.socket to a hostfs
    path so the mux process can reach it from the host, then install the
    kapsule-dbus-mux.service that listens at the normal /run/user/$uid/bus.
    """
    if not dbus_mux:
        if progress:
            progress.info("Session mode: container will use its own D-Bus session bus")
        return

    # Use uid 1000 as placeholder - the drop-in uses %t so it works for any user
    uid = 1000
    host_socket_path = f"/run/user/{uid}/kapsule/{name}/dbus.socket"

    if progress:
        progress.info(f"Configuring container D-Bus socket at: {host_socket_path}")

    # Create the directory on host with correct ownership
    kapsule_base_dir = f"/run/user/{uid}/kapsule"
    host_socket_dir = os.path.dirname(host_socket_path)
    os.makedirs(host_socket_dir, exist_ok=True)
    # Set ownership of both the kapsule base dir and container-specific dir
    os.chown(kapsule_base_dir, uid, uid)
    os.chown(host_socket_dir, uid, uid)

    # Create systemd user drop-in directory
    dropin_dir = "/etc/systemd/user/dbus.socket.d"
    try:
        await incus.mkdir(name, dropin_dir, uid=0, gid=0, mode="0755")
    except IncusError:
        pass  # Directory might already exist

    # Create the drop-in file
    systemd_socket_path = KAPSULE_DBUS_SOCKET_SYSTEMD.format(container=name)
    dropin_content = f"""[Socket]
# Kapsule: redirect D-Bus session socket to shared path
# This makes the container's D-Bus accessible from the host
# %t expands to XDG_RUNTIME_DIR (/run/user/UID)
ListenStream=
ListenStream={systemd_socket_path}
"""
    dropin_file = f"{dropin_dir}/kapsule.conf"
    try:
        await incus.push_file(name, dropin_file, dropin_content, uid=0, gid=0, mode="0644")
    except IncusError as e:
        raise OperationError(f"Failed to configure D-Bus socket: {e}")

    # Install D-Bus multiplexer service
    await _setup_dbus_mux_impl(name, incus, progress)

    # Reload systemd
    if progress:
        progress.info("Reloading systemd user configuration...")
    subprocess.run(
        ["incus", "exec", name, "--", "systemctl", "--user", "--global", "daemon-reload"],
        capture_output=True,
    )


async def _setup_dbus_mux_impl(
    name: str,
    incus: IncusClient,
    progress: OperationReporter | None,
) -> None:
    """Install kapsule-dbus-mux.service in a container."""
    if progress:
        progress.info("Installing kapsule-dbus-mux.service for D-Bus multiplexing")

    service_dir = "/etc/systemd/user"
    try:
        await incus.mkdir(name, service_dir, uid=0, gid=0, mode="0755")
    except IncusError:
        pass  # Directory might already exist

    container_dbus_socket = KAPSULE_DBUS_SOCKET_SYSTEMD.format(container=name)
    host_dbus_socket = "unix:path=/.kapsule/host%t/bus"
    mux_listen_socket = "%t/bus"

    service_content = f"""[Unit]
Description=Kapsule D-Bus Multiplexer
Documentation=man:kapsule(1)
After=dbus.service
Requires=dbus.service

[Service]
Type=simple
Environment=RUST_LOG=trace
ExecStart={KAPSULE_DBUS_MUX_BIN} \\
    --log-level debug \\
    --listen {mux_listen_socket} \\
    --container-bus unix:path={container_dbus_socket} \\
    --host-bus {host_dbus_socket}
Restart=on-failure
RestartSec=1

[Install]
WantedBy=default.target
"""

    service_file = f"{service_dir}/kapsule-dbus-mux.service"
    try:
        await incus.push_file(name, service_file, service_content, uid=0, gid=0, mode="0644")
    except IncusError as e:
        raise OperationError(f"Failed to install dbus-mux service: {e}")

    if progress:
        progress.info("Enabling kapsule-dbus-mux.service globally")
    subprocess.run(
        ["incus", "exec", name, "--", "systemctl", "--user", "--global", "enable", "kapsule-dbus-mux.service"],
        capture_output=True,
    )


async def _configure_rootless_podman_impl(
    name: str,
    incus: IncusClient,
    progress: OperationReporter | None,
) -> None:
    """Configure rootless Podman for non-session containers.

    Kapsule's default (non-session) containers forward the host's D-Bus
    session bus rather than running their own systemd user instance.
    Podman defaults to ``cgroup_manager = "systemd"`` which asks systemd
    to create a transient scope via sd-bus, but the host's systemd cannot
    manage the container's PIDs so this fails with "No such process".

    Dropping a config file into ``/etc/containers/containers.conf.d/``
    switches rootless Podman to the ``cgroupfs`` cgroup manager which
    writes cgroup entries directly instead of going through sd-bus.
    """
    parent_dir = "/etc/containers"
    dropin_dir = f"{parent_dir}/containers.conf.d"
    dropin_file = f"{dropin_dir}/50-kapsule-cgroupfs.conf"
    dropin_content = (
        "# Installed by Kapsule – non-session containers lack a systemd\n"
        "# user instance, so the default systemd cgroup manager fails.\n"
        "[engine]\n"
        'cgroup_manager = "cgroupfs"\n'
    )

    # Create the full directory hierarchy – most images don't ship
    # with Podman so /etc/containers/ won't exist yet.
    for d in (parent_dir, dropin_dir):
        try:
            await incus.mkdir(name, d, uid=0, gid=0, mode="0755")
        except IncusError:
            pass  # Directory might already exist

    try:
        await incus.push_file(
            name, dropin_file, dropin_content,
            uid=0, gid=0, mode="0644",
        )
    except IncusError as e:
        # Not fatal – best-effort config for when Podman is installed later
        if progress:
            progress.warning(f"Could not configure rootless Podman: {e}")
        return

    if progress:
        progress.dim("Configured rootless Podman (cgroup_manager=cgroupfs)")
