#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Package a mkosi output directory into Incus-compatible artifacts:
#   - incus.tar.xz  (metadata archive)
#   - rootfs.squashfs (root filesystem)
#   - version        (YYYYMMDD datestamp)
#
# If a kapsule.yaml is provided as the third argument, the image
# description and default_options are read from it and embedded into
# the Incus metadata properties so the daemon can retrieve them from
# any locally cached image.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ $# -lt 2 ] || [ $# -gt 3 ]; then
    echo "Usage: $0 <rootfs-dir> <output-dir> [kapsule.yaml]" >&2
    echo "Example: $0 mkosi.output/archlinux out/archlinux images/archlinux/kapsule.yaml" >&2
    exit 1
fi

ROOTFS_DIR="$1"
OUTPUT_DIR="$2"
KAPSULE_YAML="${3:-}"

if [ ! -d "$ROOTFS_DIR" ]; then
    echo "Error: rootfs directory '$ROOTFS_DIR' not found" >&2
    exit 1
fi

VERSION=$(date +%Y%m%d)
ARCH=$(uname -m)
CREATION_DATE=$(date +%s)

# Map uname arch to Incus arch names
case "$ARCH" in
    x86_64)  INCUS_ARCH="amd64" ;;
    aarch64) INCUS_ARCH="arm64" ;;
    *)       INCUS_ARCH="$ARCH" ;;
esac

mkdir -p "$OUTPUT_DIR"

# --- Build metadata archive ---
METADATA_DIR=$(mktemp -d)
trap 'rm -rf "$METADATA_DIR"' EXIT

generate_args=("$INCUS_ARCH" "$CREATION_DATE" "$METADATA_DIR/metadata.yaml")
if [ -n "$KAPSULE_YAML" ] && [ -f "$KAPSULE_YAML" ]; then
    generate_args+=("$KAPSULE_YAML")
fi
python3 "$SCRIPT_DIR/generate-metadata.py" "${generate_args[@]}"

tar -cf - -C "$METADATA_DIR" metadata.yaml | xz -T0 > "$OUTPUT_DIR/incus.tar.xz"

# --- Build rootfs squashfs ---
mksquashfs "$ROOTFS_DIR" "$OUTPUT_DIR/rootfs.squashfs" \
    -noappend -comp zstd -Xcompression-level 3

# --- Write version ---
echo "$VERSION" > "$OUTPUT_DIR/version"

echo "Packaged for Incus: version=$VERSION arch=$INCUS_ARCH"
echo "  $OUTPUT_DIR/incus.tar.xz"
echo "  $OUTPUT_DIR/rootfs.squashfs"
