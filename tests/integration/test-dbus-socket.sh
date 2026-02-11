#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: D-Bus socket passthrough
#
# Verifies that D-Bus socket handling differs between default and
# session mode:
#   - Default mode: the host's session bus is forwarded into the
#     container, so `busctl --user` lists host services.
#   - Session mode: the container runs its own D-Bus session bus,
#     so `busctl --user` lists only the container's own services.

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_DEFAULT="test-dbus-default"
CONTAINER_SESSION="test-dbus-session"

# Helper to run commands in the container via kapsule enter
kapsule_exec() {
    local name="$1"
    shift
    ssh_vm "kapsule enter '$name' -- $*"
}

# ============================================================================
# Setup
# ============================================================================

cleanup_container "$CONTAINER_DEFAULT"
cleanup_container "$CONTAINER_SESSION"

HOST_UID=$(ssh_vm "id -u")

echo "Testing D-Bus socket passthrough..."

# ============================================================================
# 1. Create containers (default + session mode)
# ============================================================================

echo ""
echo "1. Create default-mode container"
output=$(create_container "$CONTAINER_DEFAULT" "images:archlinux" 2>&1) || {
    echo "Create (default) failed:"
    echo "$output"
    exit 1
}
assert_container_exists "$CONTAINER_DEFAULT"
assert_container_state "$CONTAINER_DEFAULT" "RUNNING"

echo ""
echo "2. Create session-mode container"
output=$(ssh_vm "kapsule create '$CONTAINER_SESSION' --image images:archlinux --session ${KAPSULE_CREATE_FLAGS:-}" 2>&1) || {
    echo "Create (session) failed:"
    echo "$output"
    exit 1
}
assert_container_exists "$CONTAINER_SESSION"
assert_container_state "$CONTAINER_SESSION" "RUNNING"

# Give containers a moment to fully initialise
sleep 3

# ============================================================================
# 2. Trigger user setup in both containers
# ============================================================================

echo ""
echo "3. Setting up user in both containers (kapsule enter)"

# kapsule enter triggers user setup (home mount, linger, etc.) as a side
# effect.  For the session container this also starts the systemd user
# instance which creates /run/user/$uid/bus via dbus.socket.
kapsule_exec "$CONTAINER_DEFAULT" "true" 2>/dev/null
kapsule_exec "$CONTAINER_SESSION" "true" 2>/dev/null

# The session container's systemd user instance needs a moment to bring
# up dbus.socket after linger is enabled.
echo "  Waiting for session container's user bus to come up..."
retries=15
while ((retries > 0)); do
    if kapsule_exec "$CONTAINER_SESSION" "test -e /run/user/$HOST_UID/bus" 2>/dev/null; then
        break
    fi
    sleep 1
    ((retries--))
done
echo -e "  ${GREEN}✓${NC} User setup complete"

# ============================================================================
# 3. Verify mode metadata is set correctly
# ============================================================================

echo ""
echo "4. Verify container modes"

default_mode=$(ssh_vm "incus config get '$CONTAINER_DEFAULT' user.kapsule.session-mode" 2>/dev/null)
session_mode=$(ssh_vm "incus config get '$CONTAINER_SESSION' user.kapsule.session-mode" 2>/dev/null)

# Default mode should have no session-mode key (or empty)
if [[ -z "$default_mode" || "$default_mode" != "true" ]]; then
    echo -e "  ${GREEN}✓${NC} Default container does not have session-mode set"
else
    echo -e "  ${RED}✗${NC} Default container unexpectedly has session-mode=true"
    exit 1
fi

assert_eq "Session container has session-mode=true" "true" "$session_mode"

# ============================================================================
# 4. D-Bus socket symlink tests
# ============================================================================

echo ""
echo "5. Checking D-Bus socket in default-mode container"

# In default mode the bus socket should be a symlink to the host
if kapsule_exec "$CONTAINER_DEFAULT" "test -e /run/user/$HOST_UID/bus" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} D-Bus socket exists in default container"
else
    echo -e "  ${RED}✗${NC} D-Bus socket missing in default container"
    exit 1
fi

if kapsule_exec "$CONTAINER_DEFAULT" "test -L /run/user/$HOST_UID/bus" 2>/dev/null; then
    target=$(kapsule_exec "$CONTAINER_DEFAULT" "readlink /run/user/$HOST_UID/bus" 2>/dev/null)
    expected_target="/.kapsule/host/run/user/$HOST_UID/bus"
    assert_eq "Default D-Bus socket points to host" "$expected_target" "$target"
else
    echo -e "  ${RED}✗${NC} D-Bus socket is not a symlink (expected host passthrough)"
    exit 1
fi

echo ""
echo "6. Checking D-Bus socket in session-mode container"

# In session mode (without mux) the container's own systemd dbus.socket
# creates /run/user/$uid/bus natively. It should be a real socket, NOT a
# symlink to the host.
if kapsule_exec "$CONTAINER_SESSION" "test -e /run/user/$HOST_UID/bus" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} D-Bus socket exists in session container"
else
    echo -e "  ${RED}✗${NC} D-Bus socket missing in session container"
    exit 1
fi

if kapsule_exec "$CONTAINER_SESSION" "test -L /run/user/$HOST_UID/bus" 2>/dev/null; then
    target=$(kapsule_exec "$CONTAINER_SESSION" "readlink /run/user/$HOST_UID/bus" 2>/dev/null)
    host_target="/.kapsule/host/run/user/$HOST_UID/bus"
    if [[ "$target" == "$host_target" ]]; then
        echo -e "  ${RED}✗${NC} Session D-Bus socket is symlinked to host bus (should be native)"
        exit 1
    fi
    echo -e "  ${YELLOW}!${NC} D-Bus socket is a symlink to: $target (expected a real socket)"
else
    echo -e "  ${GREEN}✓${NC} D-Bus socket is NOT a symlink (container's own systemd socket)"
fi

# ============================================================================
# 5. Default mode — busctl should show host services
# ============================================================================

echo ""
echo "7. Default mode: busctl --user shows host services"

# Grab busctl output from host and container
host_busctl=$(ssh_vm "busctl --user list --no-pager" 2>&1)
default_busctl=$(kapsule_exec "$CONTAINER_DEFAULT" "busctl --user list --no-pager" 2>&1)

# The host session bus should have org.freedesktop.portal.Desktop or
# a KDE service — pick a well-known one that would never appear in a
# fresh container with its own bus.
# Try several common host-only services.
host_service=""
for svc in org.freedesktop.portal.Desktop org.kde.StatusNotifierWatcher org.kde.KWin org.freedesktop.Notifications; do
    if echo "$host_busctl" | grep -q "$svc"; then
        host_service="$svc"
        break
    fi
done

if [[ -z "$host_service" ]]; then
    echo -e "  ${YELLOW}!${NC} Could not find a distinguishing host service in busctl output"
    echo "    Host busctl snippet:"
    echo "$host_busctl" | head -20 | sed 's/^/    /'
    echo -e "  ${YELLOW}!${NC} Skipping host-service visibility check"
else
    echo "  Using host service: $host_service"
    assert_contains "Default container sees host service" "$default_busctl" "$host_service"
fi

# ============================================================================
# 6. Session mode — busctl should show container-own services only
# ============================================================================

echo ""
echo "8. Session mode: busctl --user shows own services (not host)"

session_busctl=$(kapsule_exec "$CONTAINER_SESSION" "busctl --user list --no-pager" 2>&1)

# The session container should have org.freedesktop.DBus (the bus itself)
assert_contains "Session container has org.freedesktop.DBus" "$session_busctl" "org.freedesktop.DBus"

# It should NOT have the host-only service we detected earlier
if [[ -n "$host_service" ]]; then
    if echo "$session_busctl" | grep -q "$host_service"; then
        echo -e "  ${RED}✗${NC} Session container sees host service $host_service (should not)"
        echo "    Session busctl snippet:"
        echo "$session_busctl" | head -20 | sed 's/^/    /'
        exit 1
    else
        echo -e "  ${GREEN}✓${NC} Session container does NOT see host service $host_service"
    fi
fi

# ============================================================================
# 7. Sanity: both containers can talk to *some* bus
# ============================================================================

echo ""
echo "9. Sanity: busctl --user works in both containers"

# busctl --user list should exit 0 in both
if kapsule_exec "$CONTAINER_DEFAULT" "busctl --user list --no-pager" &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} busctl --user succeeds in default container"
else
    echo -e "  ${RED}✗${NC} busctl --user failed in default container"
    exit 1
fi

if kapsule_exec "$CONTAINER_SESSION" "busctl --user list --no-pager" &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} busctl --user succeeds in session container"
else
    echo -e "  ${RED}✗${NC} busctl --user failed in session container"
    exit 1
fi

# ============================================================================
# Cleanup
# ============================================================================

echo ""
echo "10. Cleanup"
cleanup_container "$CONTAINER_DEFAULT"
cleanup_container "$CONTAINER_SESSION"
assert_container_not_exists "$CONTAINER_DEFAULT"
assert_container_not_exists "$CONTAINER_SESSION"

echo ""
echo "D-Bus socket passthrough tests passed!"
