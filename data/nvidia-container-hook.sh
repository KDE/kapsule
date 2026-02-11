#!/bin/bash

# SPDX-FileCopyrightText: 2026 KDE Community
# SPDX-License-Identifier: GPL-3.0-or-later

# Kapsule NVIDIA driver injection hook for LXC
#
# Registered as lxc.hook.mount — runs after rootfs mount, before pivot_root.
# At this stage $LXC_ROOTFS_MOUNT points to the container rootfs on the host
# and any bind-mounts created under it will be visible inside the container
# after pivot_root.
#
# --- Why this exists instead of using nvidia.runtime ---
#
# Upstream Incus rejects nvidia.runtime=true on privileged containers
# (security.privileged=true) and the upstream LXC hook
# (/usr/share/lxc/hooks/nvidia) explicitly exits with an error outside
# a user namespace.  Both restrictions stem from a limitation in
# libnvidia-container: its --user flag (required by the upstream hook)
# relies on user-namespace UID/GID remapping, and its default codepath
# expects to manage cgroups for device isolation — neither of which
# applies to privileged containers.
#
# Kapsule containers are privileged by design (for nesting, host
# networking, etc.), so we run nvidia-container-cli directly with:
#
#   --no-cgroups   Privileged containers have unrestricted device access;
#                  cgroup-based GPU isolation is unnecessary.
#   --no-devbind   Incus's gpu device type already passes /dev/nvidia*
#                  and /dev/dri/* into the container.
#
# This leaves nvidia-container-cli with only one job: bind-mount the
# host's NVIDIA userspace libraries and DSOs into the container rootfs
# so that CUDA / OpenGL / Vulkan work without the container image
# shipping its own driver stack.  That operation requires no user
# namespace support and no cgroup manipulation, so the upstream
# restrictions do not apply.
#
# See also: docs/ARCHITECTURE.md § "NVIDIA GPU Support"

set -eu

# ---- Hook-type guard -------------------------------------------------------

HOOK_TYPE=
case "${LXC_HOOK_VERSION:-0}" in
    0) HOOK_TYPE="${3:-}" ;;
    1) HOOK_TYPE="${LXC_HOOK_TYPE:-}" ;;
esac

if [ "${HOOK_TYPE}" != "mount" ]; then
    exit 0
fi

# ---- Pre-flight checks (silent exit when NVIDIA is unavailable) -------------

export PATH=$PATH:/usr/sbin:/usr/bin:/sbin:/bin

if ! command -v nvidia-container-cli >/dev/null 2>&1; then
    exit 0
fi

if [ ! -e /dev/nvidia0 ]; then
    exit 0
fi

# ---- Build nvidia-container-cli arguments -----------------------------------

ldconfig_path=$(command -v "ldconfig.real" 2>/dev/null \
             || command -v "ldconfig"      2>/dev/null \
             || true)

configure_args=(
    --no-cgroups          # No cgroup device isolation needed (privileged container)
    --no-devbind          # Incus gpu device already passes /dev/nvidia* through
    --device=all
    --compute
    --utility
    --graphics
)

if [ -n "${ldconfig_path}" ]; then
    configure_args+=(--ldconfig="@${ldconfig_path}")
fi

# ---- AppArmor transition (best-effort) --------------------------------------

if [ -d /sys/kernel/security/apparmor ]; then
    echo "changeprofile unconfined" > /proc/self/attr/current 2>/dev/null || true
fi

# ---- Inject NVIDIA userspace into the container rootfs ----------------------

exec nvidia-container-cli configure "${configure_args[@]}" "${LXC_ROOTFS_MOUNT}"
