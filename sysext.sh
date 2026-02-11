#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

set -xe

if [[ -n "$VIRTUAL_ENV" ]]; then
    echo "ERROR: This script must not be run inside a Python virtual environment." >&2
    echo "Please deactivate the venv first and try again." >&2
    exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install_dir=~/src/kde/sysext/kapsule

# Clean and recreate install directory
sudo rm -rf "$install_dir"
mkdir -p "$install_dir"

# Start by building kapsule with kde-builder
kde-builder --no-src --build-when-unchanged kapsule

# add kapsule-dbus-mux to the sysext
# this will eventually come from the AUR or something
cp /home/fernie/src/kapsule-dbus-mux/target/x86_64-unknown-linux-musl/release/kapsule-dbus-mux "$install_dir/usr/lib/kapsule/"

# Create extension-release metadata for the sysext
# This allows systemd-sysext to recognize and merge the extension
# Using ID=_any so system updates don't break the extension
extension_release_dir="$install_dir/usr/lib/extension-release.d"
sudo mkdir -p "$extension_release_dir"
sudo tee "$extension_release_dir/extension-release.kapsule" << 'EOF'
NAME="KDE Linux"
PRETTY_NAME="KDE Linux"
ID=_any
VERSION_ID="2026-01-27"
IMAGE_ID="kde-linux"
IMAGE_VERSION="202601271004"
EOF

serve_dir=~/src/kde/sysext
mkdir -p "$serve_dir"

sudo tar -cf ${serve_dir}/kapsule.tar -C "$install_dir" usr

python -m http.server --directory "$serve_dir" 8000 &
trap "kill $!" EXIT

ssh root@192.168.100.129 \
    "importctl pull-tar --class=sysext --verify=no --force http://192.168.100.1:8000/kapsule.tar && \
     systemd-sysext refresh && \
     systemctl daemon-reload && \
     systemctl restart kapsule-daemon.service"
