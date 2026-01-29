#!/bin/bash

set -xe

install_dir=~/src/kde/sysext/kapsule
sudo rm -rf "$install_dir"
mkdir -p "$install_dir"

kde-builder --no-src --build-when-unchanged kapsule
sudo pacstrap -c "$install_dir" incus

# Remove files that already exist in the KDE Linux base system
# to avoid duplicating ~430k files in the sysext
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$script_dir/kde-linux-file-list.txt" ]]; then
    echo "Removing files already in base system..."
    # Use sed to prepend install_dir to each path, xargs for efficient batch removal
    sed "s|^|${install_dir}|" "$script_dir/kde-linux-file-list.txt" | xargs -d '\n' rm -f 2>/dev/null || true
    # Clean up empty directories left behind
    find "$install_dir" -type d -empty -delete 2>/dev/null || true
fi

# Create extension-release metadata for the sysext
# This allows systemd-sysext to recognize and merge the extension
# Using ID=_any so system updates don't break the extension
extension_release_dir="$install_dir/usr/lib/extension-release.d"
mkdir -p "$extension_release_dir"
cat > "$extension_release_dir/extension-release.kapsule" << 'EOF'
NAME="KDE Linux"
PRETTY_NAME="KDE Linux"
ID=_any
VERSION_ID="2026-01-27"
IMAGE_ID="kde-linux"
IMAGE_VERSION="202601271004"
EOF

serve_dir=/tmp/kapsule
mkdir -p "$serve_dir"

sudo tar -cf ${serve_dir}/kapsule.tar -C "$install_dir" usr

python -m http.server --directory "$serve_dir" 8000 &
trap "kill $!" EXIT

ssh root@192.168.100.157 \
    "importctl pull-tar --class=sysext --verify=no --force http://192.168.100.1:8000/kapsule.tar && \
     systemd-sysext refresh && \
     systemctl daemon-reload"
