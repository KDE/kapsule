#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: image-default custom mounts (mechanic) using a synthetic image
#
# This test exercises the kapsule.default_options code path end-to-end
# on a controlled image, so we can verify expansion and bidirectional
# I/O without colliding with the developer's ~/kde source tree (the
# kapsule:kapsule-dev image's default).
#
# Setup:
#   - Copy a stock alpine image into the local store as
#     test-image-default-expansion-img
#   - Inject kapsule.default_options pointing at a unique
#     ~/.cache path that we own
#
# Exercise:
#   - kapsule create from the synthetic image
#   - Daemon should populate user.kapsule.custom-mounts from the
#     image's default_options
#   - User setup should expand ~ to the entering user's home and
#     create the mount device
#   - Bidirectional I/O between host and container through the mount
#
# Cleanup:
#   - Container delete
#   - Synthetic image alias delete
#   - Auto-created host path delete

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_NAME="test-image-default-expansion"
SYNTH_ALIAS="test-image-default-expansion-img"
SOURCE_IMAGE="images:alpine/edge"

# Use a unique per-PID path so concurrent test runs (or interrupted
# previous runs) don't interfere.
HOST_HOME=$(ssh_vm 'echo $HOME')
MOUNT_SUBDIR=".cache/kapsule-image-default-expansion-$$"
MOUNT_PATH="$HOST_HOME/$MOUNT_SUBDIR"
DEFAULTS_JSON="{\"custom_mounts\": [\"~/${MOUNT_SUBDIR}\"]}"

# ============================================================================
# Setup
# ============================================================================

cleanup_container "$CONTAINER_NAME"
ssh_vm "rm -rf '$MOUNT_PATH'"

echo "Testing image-default mount expansion (synthetic image)..."
echo "  Synthetic image alias: $SYNTH_ALIAS (based on $SOURCE_IMAGE)"
echo "  Image default mount: ~/$MOUNT_SUBDIR"
echo "  Expected expanded path: $MOUNT_PATH"

echo ""
echo "1. Build synthetic image with injected default_options"
setup_synthetic_image_with_defaults "$SYNTH_ALIAS" "$SOURCE_IMAGE" "$DEFAULTS_JSON"

# Sanity check the property was actually written.
injected=$(ssh_vm "incus image show '$SYNTH_ALIAS'" 2>/dev/null | grep "kapsule.default_options" || true)
if [[ -z "$injected" ]]; then
    echo -e "  ${RED}✗${NC} Failed to inject kapsule.default_options into image"
    teardown_synthetic_image "$SYNTH_ALIAS"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} kapsule.default_options injected"

# ============================================================================
# Exercise
# ============================================================================

echo ""
echo "2. Create container from synthetic image"
output=$(ssh_vm "kapsule create '$CONTAINER_NAME' --image local:$SYNTH_ALIAS --no-host-rootfs" 2>&1) || {
    echo "Create failed:"
    echo "$output"
    cleanup_container "$CONTAINER_NAME"
    teardown_synthetic_image "$SYNTH_ALIAS"
    exit 1
}
assert_container_exists "$CONTAINER_NAME"
assert_container_state "$CONTAINER_NAME" "RUNNING"

echo ""
echo "3. Verify daemon read default_options into container config"
mounts_raw=$(ssh_vm "incus config get '$CONTAINER_NAME' user.kapsule.custom-mounts" 2>/dev/null)
assert_contains "user.kapsule.custom-mounts contains raw ~/${MOUNT_SUBDIR}" \
    "$mounts_raw" "~/${MOUNT_SUBDIR}"

echo ""
echo "4. Trigger user setup (kapsule enter)"
ssh_vm "kapsule enter '$CONTAINER_NAME' -- true" 2>/dev/null || true
sleep 1

echo ""
echo "5. Verify expanded mount device was created"
devices=$(ssh_vm "incus config device list '$CONTAINER_NAME'" 2>/dev/null)
expected_safe_name=$(echo "$MOUNT_PATH" | sed 's|^/||; s|/|-|g; s|\.|-|g')
expected_device="kapsule-mount-${expected_safe_name}"
if [ ${#expected_device} -gt 63 ]; then
    expected_hash=$(echo -n "$MOUNT_PATH" | sha256sum | cut -c1-12)
    expected_device="kapsule-mount-${expected_hash}"
fi
assert_contains "Container has mount device for expanded path" "$devices" "$expected_device"

echo ""
echo "6. Verify host path was auto-created"
assert_success "Expanded mount path exists on host" \
    ssh_vm "test -d '$MOUNT_PATH'"

echo ""
echo "7. Verify mount is visible inside container"
assert_success "Mount path visible in container" \
    ssh_vm "incus exec '$CONTAINER_NAME' -- test -d '$MOUNT_PATH'"

echo ""
echo "8. Verify bidirectional I/O through the mount"
# host -> container
ssh_vm "echo 'from-host' > '$MOUNT_PATH/host-marker.txt'"
container_view=$(ssh_vm "incus exec '$CONTAINER_NAME' -- cat '$MOUNT_PATH/host-marker.txt'" 2>/dev/null)
assert_eq "Host write visible in container" "from-host" "$container_view"

# container -> host
ssh_vm "incus exec '$CONTAINER_NAME' -- sh -c \"echo 'from-container' > '$MOUNT_PATH/container-marker.txt'\""
host_view=$(ssh_vm "cat '$MOUNT_PATH/container-marker.txt'" 2>/dev/null)
assert_eq "Container write visible on host" "from-container" "$host_view"

# ============================================================================
# Cleanup
# ============================================================================

echo ""
echo "9. Cleanup"
cleanup_container "$CONTAINER_NAME"
teardown_synthetic_image "$SYNTH_ALIAS"
ssh_vm "rm -rf '$MOUNT_PATH'"

echo ""
echo "Image-default mount expansion test passed!"
