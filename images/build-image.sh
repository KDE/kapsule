#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Build all Kapsule images using mkosi, then package each into Incus
# artifacts (incus.tar.xz + rootfs.squashfs).
#
# Usage: sudo build-image.sh <output-dir>
# Example: sudo images/build-image.sh out/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ $# -ne 1 ]; then
    echo "Usage: sudo $0 <output-dir>" >&2
    echo "Example: sudo $0 out/" >&2
    exit 1
fi

OUTPUT_BASE="$1"

echo "Building all images with mkosi ..."
mkosi --directory="$SCRIPT_DIR" build

# Package each image output into Incus artifacts
for image_dir in "$SCRIPT_DIR"/mkosi.images/*/; do
    [ -d "$image_dir" ] || continue
    image_name=$(basename "$image_dir")
    rootfs_dir="$SCRIPT_DIR/mkosi.output/$image_name"

    if [ ! -d "$rootfs_dir" ]; then
        echo "Warning: no output for $image_name at $rootfs_dir, skipping" >&2
        continue
    fi

    echo "Packaging $image_name for Incus ..."
    "$SCRIPT_DIR/package-incus.sh" "$rootfs_dir" "$OUTPUT_BASE/$image_name"
done

echo "All images built and packaged."
