# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Helpers for building base Incus container config and devices."""

from __future__ import annotations

import json
import os

from ..container_options import ContainerOptions
from .constants import (
    KAPSULE_CUSTOM_MOUNTS_KEY,
    KAPSULE_DBUS_MUX_KEY,
    KAPSULE_GPU_KEY,
    KAPSULE_HOST_ROOTFS_KEY,
    KAPSULE_MOUNT_HOME_KEY,
    KAPSULE_NVIDIA_DRIVERS_KEY,
    KAPSULE_SESSION_MODE_KEY,
    NVIDIA_HOOK_PATH,
)


def base_container_config(nvidia_drivers: bool) -> dict[str, str]:
    """Base Incus config applied to every new Kapsule container.

    Args:
        nvidia_drivers: If True *and* the hook script is present on the host,
            register an LXC mount hook that injects NVIDIA userspace drivers
            into the container before pivot_root.

    Returns:
        Config dict with security and networking settings.
    """
    raw_lxc = "lxc.net.0.type=none\n"

    # Register NVIDIA driver injection hook when enabled.
    # We use our own hook rather than Incus's nvidia.runtime because
    # upstream rejects that option on privileged containers.  See the
    # header comment in data/nvidia-container-hook.sh for the full
    # rationale.  The hook silently exits 0 when nvidia-container-cli
    # or /dev/nvidia0 are absent, so this is safe on non-NVIDIA hosts.
    if nvidia_drivers and os.path.isfile(NVIDIA_HOOK_PATH):
        raw_lxc += f"lxc.hook.mount={NVIDIA_HOOK_PATH}\n"

    return {
        # In a future version, we might investigate what
        # we can do with unprivileged containers.
        "security.privileged": "true",
        "security.nesting": "true",
        # Use host networking (+ optional NVIDIA hook)
        "raw.lxc": raw_lxc,
    }


def base_container_devices(host_rootfs: bool, gpu: bool = True) -> dict[str, dict[str, str]]:
    """Base Incus devices applied to every new Kapsule container.

    Args:
        host_rootfs: If True, mount the entire host filesystem at /.kapsule/host.
            If False, only targeted mounts are added later during user setup.
        gpu: If True, include GPU passthrough device.

    Returns:
        Devices dict with root disk, optionally GPU passthrough, and optionally host filesystem.
    """
    devices: dict[str, dict[str, str]] = {
        # Root disk - required for container storage
        "root": {
            "type": "disk",
            "path": "/",
            "pool": "default",
        },
    }

    if gpu:
        # GPU passthrough — in privileged containers this exposes all GPU
        # device nodes (/dev/nvidia*, /dev/dri/*, etc.) automatically.
        devices["gpu"] = {
            "type": "gpu",
        }

    if host_rootfs:
        # Mount the entire host filesystem at /.kapsule/host
        devices["hostfs"] = {
            "type": "disk",
            "source": "/",
            "path": "/.kapsule/host",
            "propagation": "rslave",
            "recursive": "true",
            "shift": "false",
        }

    return devices


def store_option_metadata(config: dict[str, str], opts: ContainerOptions) -> None:
    """Store kapsule option values as ``user.kapsule.*`` config keys.

    These metadata keys are read back later by user-setup and enter
    steps to decide which features are active for a container.
    """
    if opts.session_mode:
        config[KAPSULE_SESSION_MODE_KEY] = "true"
    if opts.dbus_mux:
        config[KAPSULE_DBUS_MUX_KEY] = "true"
    config[KAPSULE_HOST_ROOTFS_KEY] = str(opts.host_rootfs).lower()
    config[KAPSULE_MOUNT_HOME_KEY] = str(opts.mount_home).lower()
    if opts.custom_mounts:
        config[KAPSULE_CUSTOM_MOUNTS_KEY] = json.dumps(opts.custom_mounts)
    config[KAPSULE_GPU_KEY] = str(opts.gpu).lower()
    config[KAPSULE_NVIDIA_DRIVERS_KEY] = str(opts.gpu and opts.nvidia_drivers).lower()
