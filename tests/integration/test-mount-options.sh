#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: Mount options (--no-mount-home, --custom-mounts, and image defaults)
#
# Verifies that:
#   - The default container mounts the user's home directory
#   - --no-mount-home prevents the home bind-mount while still
#     creating the home path for the user
#   - --custom-mounts bind-mounts extra host directories into
#     the container (tested with --no-mount-home, where custom
#     mounts are the primary way to share host directories)
#   - ~/... custom mount paths are expanded per-user and auto-created
#   - image default custom_mounts entries using ~ are expanded to the
#     entering user's home directory

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_DEFAULT="test-mounts-default"
CONTAINER_CUSTOM="test-mounts-custom"
CONTAINER_TILDE="test-mounts-tilde"
CONTAINER_IMAGE_DEFAULTS="test-mounts-image-defaults"

kapsule_exec() {
    local name="$1"
    shift
    ssh_vm "kapsule enter '$name' -- $*"
}

# ============================================================================
# Setup
# ============================================================================

cleanup_container "$CONTAINER_DEFAULT"
cleanup_container "$CONTAINER_CUSTOM"

HOST_UID=$(ssh_vm "id -u")
HOST_USER=$(ssh_vm "whoami")
HOST_HOME=$(ssh_vm 'echo $HOME')

# Create temporary directories on the host to use as custom mount sources.
# Avoid /tmp and /var/tmp because the daemon runs with PrivateTmp=true,
# which makes those paths invisible to the daemon process.
MOUNT_BASE="$HOST_HOME/.cache/kapsule-test"
ssh_vm "mkdir -p '$MOUNT_BASE'"
MOUNT_DIR_1=$(ssh_vm "mktemp -d '$MOUNT_BASE/mount-1-XXXXXX'")
MOUNT_DIR_2=$(ssh_vm "mktemp -d '$MOUNT_BASE/mount-2-XXXXXX'")

ssh_vm "echo 'mount1-content' > '$MOUNT_DIR_1/marker1.txt'"
ssh_vm "echo 'mount2-content' > '$MOUNT_DIR_2/marker2.txt'"

# A ~/... path that does NOT exist yet — should be auto-created during user setup
TILDE_SUBDIR=".cache/kapsule-test/tilde-mount-$$"
TILDE_MOUNT_DIR="$HOST_HOME/$TILDE_SUBDIR"
ssh_vm "rm -rf '$TILDE_MOUNT_DIR'"  # ensure it doesn't exist

# The kapsule-dev image ships default_options.custom_mounts: ["~/kde"]
IMAGE_DEFAULT_MOUNT="$HOST_HOME/kde"
ssh_vm "mkdir -p '$IMAGE_DEFAULT_MOUNT'"
ssh_vm "echo 'image-default-content' > '$IMAGE_DEFAULT_MOUNT/image-default-marker.txt'"

echo "Testing mount options..."
echo "  Host user: $HOST_USER (uid=$HOST_UID)"
echo "  Host home: $HOST_HOME"
echo "  Custom mount 1: $MOUNT_DIR_1"
echo "  Custom mount 2: $MOUNT_DIR_2"
echo "  Tilde mount (will be created): ~/$TILDE_SUBDIR"
echo "  Image default mount: $IMAGE_DEFAULT_MOUNT"

home_basename=$(basename "$HOST_HOME")
container_home="/home/$home_basename"
marker="kapsule-test-marker-$$"

# ============================================================================
# 1. Create containers
# ============================================================================

echo ""
echo "1. Create default container (home mounted)"
output=$(ssh_vm "kapsule create '$CONTAINER_DEFAULT' --image images:alpine/edge" 2>&1) || {
    echo "Create (default) failed:"
    echo "$output"
    exit 1
}
assert_container_exists "$CONTAINER_DEFAULT"
assert_container_state "$CONTAINER_DEFAULT" "RUNNING"

echo ""
echo "2. Create container with --no-mount-home and --custom-mounts"
output=$(ssh_vm "kapsule create '$CONTAINER_CUSTOM' --image images:alpine/edge \
    --no-mount-home \
    --custom-mounts '$MOUNT_DIR_1' \
    --custom-mounts '$MOUNT_DIR_2'" 2>&1) || {
    echo "Create (custom) failed:"
    echo "$output"
    exit 1
}
assert_container_exists "$CONTAINER_CUSTOM"
assert_container_state "$CONTAINER_CUSTOM" "RUNNING"

echo ""
echo "3. Create container with ~/... custom mount (path does not exist yet)"
output=$(ssh_vm "kapsule create '$CONTAINER_TILDE' --image images:alpine/edge \
    --no-host-rootfs \
    --no-mount-home \
    --custom-mounts '~/$TILDE_SUBDIR'" 2>&1) || {
    echo "Create (tilde) failed:"
    echo "$output"
    exit 1
}
assert_container_exists "$CONTAINER_TILDE"
assert_container_state "$CONTAINER_TILDE" "RUNNING"

echo ""
echo "4. Create container using kapsule-dev image defaults"
output=$(ssh_vm "kapsule create '$CONTAINER_IMAGE_DEFAULTS' --image kapsule:kapsule-dev --no-host-rootfs" 2>&1) || {
    echo "Create (image defaults) failed:"
    echo "$output"
    exit 1
}
assert_container_exists "$CONTAINER_IMAGE_DEFAULTS"
assert_container_state "$CONTAINER_IMAGE_DEFAULTS" "RUNNING"

sleep 2

# ============================================================================
# 2. Verify config metadata
# ============================================================================

echo ""
echo "5. Verify config keys"

default_mount_home=$(ssh_vm "incus config get '$CONTAINER_DEFAULT' user.kapsule.mount-home" 2>/dev/null)
assert_eq "Default container has mount-home=true" "true" "$default_mount_home"

custom_mount_home=$(ssh_vm "incus config get '$CONTAINER_CUSTOM' user.kapsule.mount-home" 2>/dev/null)
assert_eq "Custom container has mount-home=false" "false" "$custom_mount_home"

custom_mounts_raw=$(ssh_vm "incus config get '$CONTAINER_CUSTOM' user.kapsule.custom-mounts" 2>/dev/null)
assert_contains "Config contains mount dir 1" "$custom_mounts_raw" "$MOUNT_DIR_1"
assert_contains "Config contains mount dir 2" "$custom_mounts_raw" "$MOUNT_DIR_2"

# Tilde container: config should store the raw ~/... path (not expanded)
tilde_mounts_raw=$(ssh_vm "incus config get '$CONTAINER_TILDE' user.kapsule.custom-mounts" 2>/dev/null)
assert_contains "Tilde mount config contains raw ~/... path" "$tilde_mounts_raw" "~/$TILDE_SUBDIR"

# Image-default container: config should store the raw ~/kde path
image_defaults_mount_home=$(ssh_vm "incus config get '$CONTAINER_IMAGE_DEFAULTS' user.kapsule.mount-home" 2>/dev/null)
assert_eq "Image-default container has mount-home=false" "false" "$image_defaults_mount_home"

image_defaults_mounts_raw=$(ssh_vm "incus config get '$CONTAINER_IMAGE_DEFAULTS' user.kapsule.custom-mounts" 2>/dev/null)
assert_contains "Image-default config contains raw ~/kde path" "$image_defaults_mounts_raw" "~/kde"

# ============================================================================
# 3. Trigger user setup (kapsule enter)
# ============================================================================

echo ""
echo "6. Set up user in all containers"

kapsule_exec "$CONTAINER_DEFAULT" "true" 2>/dev/null || true
kapsule_exec "$CONTAINER_CUSTOM" "true" 2>/dev/null || true
kapsule_exec "$CONTAINER_TILDE" "true" 2>/dev/null || true
kapsule_exec "$CONTAINER_IMAGE_DEFAULTS" "true" 2>/dev/null || true

sleep 1

# ============================================================================
# 4. Default container: home directory is mounted
# ============================================================================

echo ""
echo "7. Verify default container has home directory mounted"

default_devices=$(ssh_vm "incus config device list '$CONTAINER_DEFAULT'" 2>/dev/null)
assert_contains "Default container has home device" "$default_devices" "kapsule-home-${HOST_USER}"

ssh_vm "touch '$HOST_HOME/$marker'"

assert_success "Default container: host home file visible" \
    ssh_vm "incus exec '$CONTAINER_DEFAULT' -- test -f '$container_home/$marker'"

ssh_vm "rm -f '$HOST_HOME/$marker'"

# ============================================================================
# 5. Custom container: home directory is NOT mounted
# ============================================================================

echo ""
echo "8. Verify custom container does not have home device"

custom_devices=$(ssh_vm "incus config device list '$CONTAINER_CUSTOM'" 2>/dev/null)

if [[ "$custom_devices" == *"kapsule-home-${HOST_USER}"* ]]; then
    echo -e "  ${RED}✗${NC} Custom container should not have kapsule-home device"
    exit 1
else
    echo -e "  ${GREEN}✓${NC} Custom container does not have kapsule-home device"
fi

# Home path should still exist (created for the user account)
assert_success "Custom container: home directory path exists" \
    ssh_vm "incus exec '$CONTAINER_CUSTOM' -- test -d '$container_home'"

# But host files should NOT be visible
ssh_vm "touch '$HOST_HOME/$marker'"

assert_failure "Custom container: host home file NOT visible" \
    ssh_vm "incus exec '$CONTAINER_CUSTOM' -- test -f '$container_home/$marker'"

ssh_vm "rm -f '$HOST_HOME/$marker'"

# Container should still be functional without the home mount
assert_success "Custom container: can write to container home" \
    ssh_vm "incus exec '$CONTAINER_CUSTOM' -- touch '$container_home/test-file'"

assert_success "Custom container: can read written file" \
    ssh_vm "incus exec '$CONTAINER_CUSTOM' -- test -f '$container_home/test-file'"

# ============================================================================
# 6. Custom container: custom mount devices are present
# ============================================================================

echo ""
echo "9. Verify custom mount devices"

safe_name_1=$(echo "$MOUNT_DIR_1" | sed 's|^/||; s|/|-|g; s|\.|-|g')
safe_name_2=$(echo "$MOUNT_DIR_2" | sed 's|^/||; s|/|-|g; s|\.|-|g')

assert_contains "Custom container has device for mount 1" "$custom_devices" "kapsule-mount-${safe_name_1}"
assert_contains "Custom container has device for mount 2" "$custom_devices" "kapsule-mount-${safe_name_2}"

# ============================================================================
# 7. Custom container: mounted content is accessible
# ============================================================================

echo ""
echo "10. Verify custom mount contents are accessible"

assert_success "Mount dir 1 exists in container" \
    ssh_vm "incus exec '$CONTAINER_CUSTOM' -- test -d '$MOUNT_DIR_1'"

assert_success "Mount dir 2 exists in container" \
    ssh_vm "incus exec '$CONTAINER_CUSTOM' -- test -d '$MOUNT_DIR_2'"

marker1_content=$(ssh_vm "incus exec '$CONTAINER_CUSTOM' -- cat '$MOUNT_DIR_1/marker1.txt'" 2>/dev/null)
assert_eq "Marker file 1 has correct content" "mount1-content" "$marker1_content"

marker2_content=$(ssh_vm "incus exec '$CONTAINER_CUSTOM' -- cat '$MOUNT_DIR_2/marker2.txt'" 2>/dev/null)
assert_eq "Marker file 2 has correct content" "mount2-content" "$marker2_content"

# ============================================================================
# 8. Custom container: bidirectional I/O through mounts
# ============================================================================

echo ""
echo "11. Verify bidirectional I/O through custom mounts"

# Host → container
ssh_vm "echo 'updated-content' > '$MOUNT_DIR_1/live-test.txt'"
live_content=$(ssh_vm "incus exec '$CONTAINER_CUSTOM' -- cat '$MOUNT_DIR_1/live-test.txt'" 2>/dev/null)
assert_eq "Live host write visible in container" "updated-content" "$live_content"

# Container → host
ssh_vm "incus exec '$CONTAINER_CUSTOM' -- sh -c \"echo 'from-container' > '$MOUNT_DIR_2/container-write.txt'\""
host_content=$(ssh_vm "cat '$MOUNT_DIR_2/container-write.txt'" 2>/dev/null)
assert_eq "Container write visible on host" "from-container" "$host_content"

# ============================================================================
# 9. Tilde mount: auto-created and writable
# ============================================================================

echo ""
echo "12. Verify tilde mount path was auto-created on host"

assert_success "Tilde mount dir exists on host" \
    ssh_vm "test -d '$TILDE_MOUNT_DIR'"

# Check ownership
tilde_owner=$(ssh_vm "stat -c '%u:%g' '$TILDE_MOUNT_DIR'" 2>/dev/null)
assert_eq "Tilde mount dir owned by user" "$HOST_UID:$HOST_UID" "$tilde_owner"

tilde_devices=$(ssh_vm "incus config device list '$CONTAINER_TILDE'" 2>/dev/null)
tilde_safe_name=$(echo "$TILDE_MOUNT_DIR" | sed 's|^/||; s|/|-|g; s|\.-|-|g')
assert_contains "Tilde container has mount device" "$tilde_devices" "kapsule-mount-${tilde_safe_name}"

assert_success "Tilde mount dir exists in container" \
    ssh_vm "incus exec '$CONTAINER_TILDE' -- test -d '$TILDE_MOUNT_DIR'"

assert_success "Container can write through tilde mount" \
    ssh_vm "incus exec '$CONTAINER_TILDE' -- sh -c \"echo 'tilde-content' > '$TILDE_MOUNT_DIR/tilde.txt'\""

tilde_content=$(ssh_vm "cat '$TILDE_MOUNT_DIR/tilde.txt'" 2>/dev/null)
assert_eq "Tilde mount write visible on host" "tilde-content" "$tilde_content"

# ============================================================================
# 10. Image-default container: ~/kde mount is expanded and accessible
# ============================================================================

echo ""
echo "13. Verify image-default custom mount from ~/kde"

image_default_devices=$(ssh_vm "incus config device list '$CONTAINER_IMAGE_DEFAULTS'" 2>/dev/null)
image_default_safe_name=$(echo "$IMAGE_DEFAULT_MOUNT" | sed 's|^/||; s|/|-|g; s|\.-|-|g')
assert_contains "Image-default container has ~/kde mount device" "$image_default_devices" "kapsule-mount-${image_default_safe_name}"

assert_success "Image-default mount dir exists in container" \
    ssh_vm "incus exec '$CONTAINER_IMAGE_DEFAULTS' -- test -d '$IMAGE_DEFAULT_MOUNT'"

image_marker=$(ssh_vm "incus exec '$CONTAINER_IMAGE_DEFAULTS' -- cat '$IMAGE_DEFAULT_MOUNT/image-default-marker.txt'" 2>/dev/null)
assert_eq "Image-default marker file has correct content" "image-default-content" "$image_marker"

# ============================================================================
# Cleanup
# ============================================================================

echo ""
echo "14. Cleanup"
cleanup_container "$CONTAINER_DEFAULT"
cleanup_container "$CONTAINER_CUSTOM"
cleanup_container "$CONTAINER_TILDE"
cleanup_container "$CONTAINER_IMAGE_DEFAULTS"
ssh_vm "rm -rf '$MOUNT_DIR_1' '$MOUNT_DIR_2' '$TILDE_MOUNT_DIR' '$IMAGE_DEFAULT_MOUNT'"

echo ""
echo "All mount option tests passed!"
