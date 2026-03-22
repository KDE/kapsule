#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: Mount options (--no-mount-home and --custom-mounts)
#
# Verifies that:
#   - The default container mounts the user's home directory
#   - --no-mount-home prevents the home bind-mount while still
#     creating the home path for the user
#   - --custom-mounts bind-mounts extra host directories into
#     the container (tested with --no-mount-home, where custom
#     mounts are the primary way to share host directories)

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_DEFAULT="test-mounts-default"
CONTAINER_CUSTOM="test-mounts-custom"

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

echo "Testing mount options..."
echo "  Host user: $HOST_USER (uid=$HOST_UID)"
echo "  Host home: $HOST_HOME"
echo "  Custom mount 1: $MOUNT_DIR_1"
echo "  Custom mount 2: $MOUNT_DIR_2"

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

sleep 2

# ============================================================================
# 2. Verify config metadata
# ============================================================================

echo ""
echo "3. Verify config keys"

default_mount_home=$(ssh_vm "incus config get '$CONTAINER_DEFAULT' user.kapsule.mount-home" 2>/dev/null)
assert_eq "Default container has mount-home=true" "true" "$default_mount_home"

custom_mount_home=$(ssh_vm "incus config get '$CONTAINER_CUSTOM' user.kapsule.mount-home" 2>/dev/null)
assert_eq "Custom container has mount-home=false" "false" "$custom_mount_home"

custom_mounts_raw=$(ssh_vm "incus config get '$CONTAINER_CUSTOM' user.kapsule.custom-mounts" 2>/dev/null)
assert_contains "Config contains mount dir 1" "$custom_mounts_raw" "$MOUNT_DIR_1"
assert_contains "Config contains mount dir 2" "$custom_mounts_raw" "$MOUNT_DIR_2"

# ============================================================================
# 3. Trigger user setup (kapsule enter)
# ============================================================================

echo ""
echo "4. Set up user in both containers"

kapsule_exec "$CONTAINER_DEFAULT" "true" 2>/dev/null || true
kapsule_exec "$CONTAINER_CUSTOM" "true" 2>/dev/null || true

sleep 1

# ============================================================================
# 4. Default container: home directory is mounted
# ============================================================================

echo ""
echo "5. Verify default container has home directory mounted"

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
echo "6. Verify custom container does not have home device"

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
echo "7. Verify custom mount devices"

safe_name_1=$(echo "$MOUNT_DIR_1" | sed 's|^/||; s|/|-|g; s|\.|-|g')
safe_name_2=$(echo "$MOUNT_DIR_2" | sed 's|^/||; s|/|-|g; s|\.|-|g')

assert_contains "Custom container has device for mount 1" "$custom_devices" "kapsule-mount-${safe_name_1}"
assert_contains "Custom container has device for mount 2" "$custom_devices" "kapsule-mount-${safe_name_2}"

# ============================================================================
# 7. Custom container: mounted content is accessible
# ============================================================================

echo ""
echo "8. Verify custom mount contents are accessible"

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
echo "9. Verify bidirectional I/O through custom mounts"

# Host → container
ssh_vm "echo 'updated-content' > '$MOUNT_DIR_1/live-test.txt'"
live_content=$(ssh_vm "incus exec '$CONTAINER_CUSTOM' -- cat '$MOUNT_DIR_1/live-test.txt'" 2>/dev/null)
assert_eq "Live host write visible in container" "updated-content" "$live_content"

# Container → host
ssh_vm "incus exec '$CONTAINER_CUSTOM' -- sh -c \"echo 'from-container' > '$MOUNT_DIR_2/container-write.txt'\""
host_content=$(ssh_vm "cat '$MOUNT_DIR_2/container-write.txt'" 2>/dev/null)
assert_eq "Container write visible on host" "from-container" "$host_content"

# ============================================================================
# Cleanup
# ============================================================================

echo ""
echo "10. Cleanup"
cleanup_container "$CONTAINER_DEFAULT"
cleanup_container "$CONTAINER_CUSTOM"
ssh_vm "rm -rf '$MOUNT_DIR_1' '$MOUNT_DIR_2'"

echo ""
echo "All mount option tests passed!"
