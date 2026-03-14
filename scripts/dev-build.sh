#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail

REMOTE="${KAPSULE_VM:-192.168.100.129}"
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
if ! sudo images/build-image.sh "$IMAGE_DIR/" "$OUTPUT_DIR/"; then
    echo "Error: build failed for $IMAGE" >&2
    exit 1
fi

echo "Deploying $IMAGE to $REMOTE ..."
# Stage under /var/lib/kapsule/imports/ which the daemon can access
# (the systemd unit has ProtectSystem=strict + PrivateTmp=true)
REMOTE_DIR="/var/lib/kapsule/imports/$IMAGE"
ssh "root@$REMOTE" "mkdir -p $REMOTE_DIR"
scp "$OUTPUT_DIR/incus.tar.xz" "$OUTPUT_DIR/rootfs.squashfs" "root@$REMOTE:$REMOTE_DIR/"

echo "Importing $IMAGE on $REMOTE ..."
ssh "fernie@$REMOTE" "kapsule image import $REMOTE_DIR/ --alias $IMAGE"
ssh "root@$REMOTE" "rm -rf $REMOTE_DIR"

echo "Image ready. Use: kapsule create <name> -i local:$IMAGE"
