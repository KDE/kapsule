# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Constants shared across the container package."""

from __future__ import annotations

from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

# Config keys for kapsule metadata stored in container config
KAPSULE_SESSION_MODE_KEY = "user.kapsule.session-mode"
KAPSULE_DBUS_MUX_KEY = "user.kapsule.dbus-mux"
KAPSULE_HOST_ROOTFS_KEY = "user.kapsule.host-rootfs"
KAPSULE_MOUNT_HOME_KEY = "user.kapsule.mount-home"
KAPSULE_CUSTOM_MOUNTS_KEY = "user.kapsule.custom-mounts"
KAPSULE_GPU_KEY = "user.kapsule.gpu"
KAPSULE_NVIDIA_DRIVERS_KEY = "user.kapsule.nvidia-drivers"

# Absolute path to the NVIDIA container hook script (installed by CMake)
NVIDIA_HOOK_PATH = "/usr/lib/kapsule/nvidia-container-hook.sh"

# Path to kapsule-dbus-mux binary inside container (via hostfs mount)
KAPSULE_DBUS_MUX_BIN = "/.kapsule/host/usr/lib/kapsule/kapsule-dbus-mux"

# D-Bus socket path template using %t (systemd specifier for XDG_RUNTIME_DIR)
KAPSULE_DBUS_SOCKET_USER_PATH = "kapsule/{container}/dbus.socket"
KAPSULE_DBUS_SOCKET_SYSTEMD = "/.kapsule/host%t/" + KAPSULE_DBUS_SOCKET_USER_PATH

# Environment variables to skip when passing through to container
ENTER_ENV_SKIP = frozenset({
    "_",              # Last command (set by shell)
    "SHLVL",          # Shell nesting level
    "OLDPWD",         # Previous directory
    "PWD",            # Current directory (will be wrong in container)
    "HOSTNAME",       # Host's hostname
    "HOST",           # Host's hostname (zsh)
    "LS_COLORS",      # Often huge and causes issues
    "LESS_TERMCAP_mb", "LESS_TERMCAP_md", "LESS_TERMCAP_me",  # Less colors
    "LESS_TERMCAP_se", "LESS_TERMCAP_so", "LESS_TERMCAP_ue", "LESS_TERMCAP_us",
})


@dataclass(frozen=True)
class BindMount:
    source: str
    target: str
    uid: int
    gid: int
