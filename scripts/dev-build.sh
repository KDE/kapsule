#!/bin/bash
set -euo pipefail

IMAGE="${1:-archlinux}"
IMAGE_DIR="images/$IMAGE"

if [ ! -d "$IMAGE_DIR" ]; then
    echo "Error: $IMAGE_DIR/ not found" >&2
    echo "Usage: $0 [image-name]" >&2
    echo "Example: $0 archlinux" >&2
    exit 1
fi

OUTPUT_DIR="out/$IMAGE"

echo "Building $IMAGE ..."
if ! sudo scripts/build-image.sh "$IMAGE_DIR/" "$OUTPUT_DIR/"; then
    echo "Error: build failed for $IMAGE" >&2
    exit 1
fi

echo "Importing $IMAGE into local Incus store ..."
kapsule image import "$OUTPUT_DIR/" --alias "$IMAGE"

echo "Image ready. Use: kapsule create <name> -i local:$IMAGE"
