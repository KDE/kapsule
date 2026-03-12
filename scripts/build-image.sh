#!/bin/bash
set -euo pipefail

if [ $# -ne 2 ]; then
    echo "Usage: sudo $0 <image-dir> <output-dir>" >&2
    echo "Example: sudo $0 images/archlinux/ out/archlinux/" >&2
    exit 1
fi

IMAGE_DIR="$1"
OUTPUT_DIR="$2"
IMAGE_YAML="${IMAGE_DIR}/image.yaml"

if [ ! -f "$IMAGE_YAML" ]; then
    echo "Error: $IMAGE_YAML not found" >&2
    exit 1
fi

VERSION=$(date +%Y%m%d)

# Create a temporary build directory
BUILD_DIR=$(mktemp -d)
trap 'rm -rf "$BUILD_DIR"' EXIT

echo "Building image from $IMAGE_YAML ..."
distrobuilder build-incus "$IMAGE_YAML" "$BUILD_DIR"

# Create output directory structure
mkdir -p "$OUTPUT_DIR"

echo "Moving build artifacts to $OUTPUT_DIR ..."
mv "$BUILD_DIR/incus.tar.xz" "$OUTPUT_DIR/incus.tar.xz"
mv "$BUILD_DIR/rootfs.squashfs" "$OUTPUT_DIR/rootfs.squashfs"

# Write version file
echo "$VERSION" > "$OUTPUT_DIR/version"

echo "Build complete: version=$VERSION"
echo "  $OUTPUT_DIR/incus.tar.xz"
echo "  $OUTPUT_DIR/rootfs.squashfs"
