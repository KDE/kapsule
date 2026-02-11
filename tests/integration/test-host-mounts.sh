#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: Host filesystem mount configuration
#
# Verifies that the host-rootfs flag controls which mounts are
# present in the container:
#   - Full rootfs (default): entire host at /.kapsule/host
#   - Minimal (--no-host-rootfs): only targeted mounts for
#     /run/user/<uid> and /tmp/.X11-unix after user setup

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_FULL="test-mounts-full"
CONTAINER_MINIMAL="test-mounts-minimal"

kapsule_exec() {
    local name="$1"
    shift
    ssh_vm "kapsule enter '$name' -- $*"
}

# ============================================================================
# Setup
# ============================================================================

cleanup_container "$CONTAINER_FULL"
cleanup_container "$CONTAINER_MINIMAL"

HOST_UID=$(ssh_vm "id -u")

echo "Testing host filesystem mount configuration..."

# ============================================================================
# 1. Create containers in both modes
# ============================================================================

echo ""
echo "1. Create full-rootfs container"
output=$(ssh_vm "kapsule create '$CONTAINER_FULL' --image images:alpine/edge" 2>&1) || {
    echo "Create (full) failed:"
    echo "$output"
    exit 1
}
assert_container_exists "$CONTAINER_FULL"
assert_container_state "$CONTAINER_FULL" "RUNNING"

echo ""
echo "2. Create minimal-mount container"
output=$(ssh_vm "kapsule create '$CONTAINER_MINIMAL' --image images:alpine/edge --no-host-rootfs" 2>&1) || {
    echo "Create (minimal) failed:"
    echo "$output"
    exit 1
}
assert_container_exists "$CONTAINER_MINIMAL"
assert_container_state "$CONTAINER_MINIMAL" "RUNNING"

sleep 2

# ============================================================================
# 2. Verify config metadata
# ============================================================================

echo ""
echo "3. Verify host-rootfs config keys"

full_rootfs=$(ssh_vm "incus config get '$CONTAINER_FULL' user.kapsule.host-rootfs" 2>/dev/null)
assert_eq "Full container has host-rootfs=true" "true" "$full_rootfs"

minimal_rootfs=$(ssh_vm "incus config get '$CONTAINER_MINIMAL' user.kapsule.host-rootfs" 2>/dev/null)
assert_eq "Minimal container has host-rootfs=false" "false" "$minimal_rootfs"

# ============================================================================
# 3. Verify devices on full-rootfs container
# ============================================================================

echo ""
echo "4. Verify full-rootfs container devices"

full_devices=$(ssh_vm "incus config device list '$CONTAINER_FULL'" 2>/dev/null)
assert_contains "Full container has hostfs device" "$full_devices" "hostfs"

# Full host root should be accessible
assert_success "Full container: /.kapsule/host/usr exists" \
    ssh_vm "incus exec '$CONTAINER_FULL' -- test -d /.kapsule/host/usr"
assert_success "Full container: /.kapsule/host/etc exists" \
    ssh_vm "incus exec '$CONTAINER_FULL' -- test -d /.kapsule/host/etc"

# ============================================================================
# 4. Verify devices on minimal container (before user setup)
# ============================================================================

echo ""
echo "5. Verify minimal container devices (before user setup)"

minimal_devices=$(ssh_vm "incus config device list '$CONTAINER_MINIMAL'" 2>/dev/null)

# Should NOT have the full hostfs mount
if [[ "$minimal_devices" == *"hostfs"* ]]; then
    echo -e "  ${RED}✗${NC} Minimal container should not have hostfs device"
    exit 1
else
    echo -e "  ${GREEN}✓${NC} Minimal container does not have hostfs device"
fi

# Host /usr should not be reachable
assert_failure "Minimal container: /.kapsule/host/usr does not exist" \
    ssh_vm "incus exec '$CONTAINER_MINIMAL' -- test -d /.kapsule/host/usr"

# ============================================================================
# 5. Trigger user setup and verify targeted mounts appear
# ============================================================================

echo ""
echo "6. Set up user in minimal container (kapsule enter)"

# kapsule enter triggers user setup (device additions). The enter command
# itself may fail on minimal images (e.g. Alpine lacks bash/useradd), but
# the daemon-side setup (device additions) still runs. We only care about
# whether the devices were added.
kapsule_exec "$CONTAINER_MINIMAL" "true" 2>/dev/null || true

# Re-read devices after user setup
minimal_devices_after=$(ssh_vm "incus config device list '$CONTAINER_MINIMAL'" 2>/dev/null)

assert_contains "Minimal container has hostrun device" "$minimal_devices_after" "kapsule-hostrun-${HOST_UID}"
assert_contains "Minimal container has x11 device" "$minimal_devices_after" "kapsule-x11"

# Targeted paths should now be accessible
assert_success "Minimal container: /.kapsule/host/run/user/$HOST_UID exists" \
    ssh_vm "incus exec '$CONTAINER_MINIMAL' -- test -d /.kapsule/host/run/user/$HOST_UID"

assert_success "Minimal container: /.kapsule/host/tmp/.X11-unix exists" \
    ssh_vm "incus exec '$CONTAINER_MINIMAL' -- test -d /.kapsule/host/tmp/.X11-unix"

# Host /usr should still not be reachable (only targeted mounts)
assert_failure "Minimal container: /.kapsule/host/usr still does not exist" \
    ssh_vm "incus exec '$CONTAINER_MINIMAL' -- test -d /.kapsule/host/usr"

# ============================================================================
# 6. Also trigger user setup in full container for completeness
# ============================================================================

echo ""
echo "7. Set up user in full container (kapsule enter)"

kapsule_exec "$CONTAINER_FULL" "true" 2>/dev/null || true

full_devices_after=$(ssh_vm "incus config device list '$CONTAINER_FULL'" 2>/dev/null)

# Full container should NOT get the extra per-user mounts (hostfs covers everything)
if [[ "$full_devices_after" == *"kapsule-hostrun"* ]]; then
    echo -e "  ${RED}✗${NC} Full container should not have kapsule-hostrun device"
    exit 1
else
    echo -e "  ${GREEN}✓${NC} Full container does not have kapsule-hostrun device (hostfs covers it)"
fi

# ============================================================================
# Cleanup
# ============================================================================

echo ""
echo "8. Cleanup"
cleanup_container "$CONTAINER_FULL"
cleanup_container "$CONTAINER_MINIMAL"

echo ""
echo "All host mount tests passed!"
