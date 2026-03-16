#!/bin/bash
# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Remove any kapsule sysext overlay and revert to the OS-shipped version.
#
# Usage: sudo kapsule-uninstall-branch
#
# This removes a sysext previously installed by kapsule-install-branch,
# restoring the kapsule version that shipped with the OS image.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This script must be run as root" >&2
    echo "Usage: sudo $0" >&2
    exit 1
fi

if [ ! -f /var/lib/extensions/kapsule.raw ]; then
    echo "No kapsule sysext is installed. Already using the OS version."
    exit 0
fi

echo "Removing kapsule sysext overlay ..."
rm -f /var/lib/extensions/kapsule.raw

echo "Refreshing sysext ..."
systemd-sysext refresh

echo "Restarting kapsule daemon ..."
systemctl daemon-reload
systemctl restart kapsule-daemon.service

echo "Done. Reverted to the OS-shipped kapsule version."
systemd-sysext status
