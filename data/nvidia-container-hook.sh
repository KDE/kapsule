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
# Unlike the upstream /usr/share/lxc/hooks/nvidia, this hook works with
# *privileged* containers (security.privileged=true).

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
    --no-cgroups
    --no-devbind          # Incus gpu device already passes device nodes through
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
