#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: image-default custom mounts (smoke) using kapsule:kapsule-dev
#
# kapsule:kapsule-dev ships default_options.custom_mounts: ["~/kde"].
# When a user creates a container from this image, the daemon should:
#   1. Read kapsule.default_options from image metadata
#   2. Store the raw "~/kde" entry in user.kapsule.custom-mounts
#   3. At user setup time, expand "~/kde" to the entering user's home
#      and add a kapsule-mount-* device pointing at it
#
# This is a READ-ONLY smoke test. It does NOT mkdir, write, or rm
# anything inside ~/kde — that path is typically the developer's
# real source tree on the test VM. The destructive coverage of
# "image defaults plus bidirectional I/O" lives in
# test-image-default-expansion.sh, which uses a synthetic image
# pointing at a path under ~/.cache.

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_NAME="test-image-default-mounts"

# ============================================================================
# Setup
# ============================================================================

cleanup_container "$CONTAINER_NAME"

HOST_USER=$(ssh_vm "whoami")
HOST_HOME=$(ssh_vm 'echo $HOME')
EXPECTED_MOUNT="$HOST_HOME/kde"

echo "Testing kapsule-dev image-default mounts (smoke)..."
echo "  Host user: $HOST_USER"
echo "  Expected mount path: $EXPECTED_MOUNT"

# ============================================================================
# 1. Create kapsule-dev container
# ============================================================================

echo ""
echo "1. Create kapsule-dev container"
output=$(ssh_vm "kapsule create '$CONTAINER_NAME' --image kapsule:kapsule-dev --no-host-rootfs" 2>&1) || {
    echo "Create failed:"
    echo "$output"
    cleanup_container "$CONTAINER_NAME"
    exit 1
}
assert_container_exists "$CONTAINER_NAME"
assert_container_state "$CONTAINER_NAME" "RUNNING"

# ============================================================================
# 2. Verify the daemon read the image-default custom_mounts
# ============================================================================

echo ""
echo "2. Verify image defaults were applied"

mounts_raw=$(ssh_vm "incus config get '$CONTAINER_NAME' user.kapsule.custom-mounts" 2>/dev/null)
assert_contains "user.kapsule.custom-mounts contains raw ~/kde" "$mounts_raw" "~/kde"

mount_home=$(ssh_vm "incus config get '$CONTAINER_NAME' user.kapsule.mount-home" 2>/dev/null)
assert_eq "kapsule-dev image disables mount-home by default" "false" "$mount_home"

# ============================================================================
# 3. Trigger user setup so devices get materialized
# ============================================================================

echo ""
echo "3. Trigger user setup (kapsule enter)"
ssh_vm "kapsule enter '$CONTAINER_NAME' -- true" 2>/dev/null || true
sleep 1

# ============================================================================
# 4. Verify the device was created with the expanded path
# ============================================================================

echo ""
echo "4. Verify mount device exists"

devices=$(ssh_vm "incus config device list '$CONTAINER_NAME'" 2>/dev/null)
expected_safe_name=$(echo "$EXPECTED_MOUNT" | sed 's|^/||; s|/|-|g; s|\.|-|g')
expected_device="kapsule-mount-${expected_safe_name}"
if [ ${#expected_device} -gt 63 ]; then
    expected_hash=$(echo -n "$EXPECTED_MOUNT" | sha256sum | cut -c1-12)
    expected_device="kapsule-mount-${expected_hash}"
fi
assert_contains "Container has expanded ~/kde mount device" "$devices" "$expected_device"

# ============================================================================
# 5. If ~/kde exists on the host, verify it is visible inside the container
#    (read-only check; do NOT touch the directory)
# ============================================================================

if ssh_vm "test -d '$EXPECTED_MOUNT'"; then
    echo ""
    echo "5. ~/kde exists on host — verifying it is visible inside the container"
    assert_success "Container sees ~/kde directory" \
        ssh_vm "incus exec '$CONTAINER_NAME' -- test -d '$EXPECTED_MOUNT'"
else
    echo ""
    echo "5. ~/kde does not exist on host — skipping content visibility check"
fi

# ============================================================================
# Cleanup
# ============================================================================

echo ""
echo "6. Cleanup"
cleanup_container "$CONTAINER_NAME"
# NOTE: we deliberately do NOT touch $EXPECTED_MOUNT. It either belongs
# to the developer (on dev VMs) or simply does not exist (on CI VMs).

echo ""
echo "kapsule-dev image-default mount smoke test passed!"
