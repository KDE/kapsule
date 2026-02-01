#!/bin/bash

set -xe

# Parse CLI options
clean=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --clean)
            clean=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--clean]"
            echo "  --clean    Force re-run pacstrap even if cache exists"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pacstrap_dir=~/src/kde/sysext/kapsule-pacstrap
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

# Clean pacstrap cache if requested
if [[ "$clean" == true ]] && [[ -d "$pacstrap_dir" ]]; then
    echo "Cleaning pacstrap cache..."
    sudo rm -rf "$pacstrap_dir"
fi

# Run pacstrap only if cache doesn't exist
if [[ ! -d "$pacstrap_dir" ]]; then
    echo "Running pacstrap..."
    mkdir -p "$pacstrap_dir"
    sudo pacstrap -c "$pacstrap_dir" incus
    
    # Remove files that already exist in the KDE Linux base system
    if [[ -f "$script_dir/kde-linux-file-list.txt" ]]; then
        echo "Removing base system files from pacstrap cache..."
        sed "s|^|${pacstrap_dir}|" "$script_dir/kde-linux-file-list.txt" | xargs -d '\n' sudo rm -f 2>/dev/null || true
        sudo find "$pacstrap_dir" -type d -empty -delete 2>/dev/null || true
    fi
else
    echo "Using cached pacstrap directory..."
fi

# Hardlink files from pacstrap cache to install directory
echo "Hardlinking files to install directory..."
sudo cp -al "$pacstrap_dir/usr/." "$install_dir/usr/"

serve_dir=/tmp/kapsule
mkdir -p "$serve_dir"

sudo tar -cf ${serve_dir}/kapsule.tar -C "$install_dir" usr

python -m http.server --directory "$serve_dir" 8000 &
trap "kill $!" EXIT

ssh root@192.168.100.157 \
    "importctl pull-tar --class=sysext --verify=no --force http://192.168.100.1:8000/kapsule.tar && \
     systemd-sysext refresh && \
     systemctl daemon-reload"
