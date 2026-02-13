#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Helper functions for Kapsule integration tests
#
# Source this file in test scripts:
#   source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Test VM configuration
TEST_VM="${KAPSULE_TEST_VM:-192.168.100.129}"
VM_EXEC_MODE="${KAPSULE_VM_EXEC_MODE:-auto}"
SSH_OPTS="-n -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o LogLevel=ERROR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ============================================================================
# SSH Helpers
# ============================================================================

is_local_vm_target() {
    [[ "$TEST_VM" == "localhost" || "$TEST_VM" == "127.0.0.1" || "$TEST_VM" == "::1" ]]
}

resolve_vm_exec_mode() {
    if [[ "$VM_EXEC_MODE" == "auto" ]]; then
        if is_local_vm_target; then
            echo "local"
        else
            echo "ssh"
        fi
        return
    fi

    if [[ "$VM_EXEC_MODE" == "local" || "$VM_EXEC_MODE" == "ssh" ]]; then
        echo "$VM_EXEC_MODE"
        return
    fi

    echo "ERROR: Invalid KAPSULE_VM_EXEC_MODE='$VM_EXEC_MODE' (expected auto|local|ssh)" >&2
    return 1
}

ssh_vm() {
    local mode
    mode="$(resolve_vm_exec_mode)" || return 1

    if [[ "$mode" == "local" ]]; then
        if [[ $# -eq 1 ]]; then
            timeout --kill-after=2 25 bash -lc "$1"
        else
            timeout --kill-after=2 25 "$@"
        fi
    else
        timeout --kill-after=2 25 ssh $SSH_OPTS "$TEST_VM" "$@"
    fi
}

# ============================================================================
# Container Helpers
# ============================================================================

# Create a test container using kapsule CLI
# Usage: create_container NAME [IMAGE]
#
# Respects KAPSULE_CREATE_FLAGS env var for additional flags
# (e.g., KAPSULE_CREATE_FLAGS="--no-host-rootfs" to test minimal mounts)
create_container() {
    local name="$1"
    local image="${2:-images:alpine/edge}"
    
    ssh_vm "kapsule create '$name' --image '$image' ${KAPSULE_CREATE_FLAGS:-}" 2>&1
}

# Delete a test container
# Usage: delete_container NAME [--force]
delete_container() {
    local name="$1"
    local force="${2:-}"
    
    if [[ "$force" == "--force" ]]; then
        ssh_vm "kapsule rm '$name' --force" 2>&1
    else
        ssh_vm "kapsule rm '$name'" 2>&1
    fi
}

# Check if container exists
# Usage: container_exists NAME
container_exists() {
    local name="$1"
    ssh_vm "incus info '$name'" &>/dev/null
}

# Get container state
# Usage: container_state NAME
container_state() {
    local name="$1"
    ssh_vm "incus info '$name' 2>/dev/null | grep -E '^Status:' | awk '{print \$2}'"
}

# Wait for container to reach a state
# Usage: wait_for_state NAME STATE [TIMEOUT]
wait_for_state() {
    local name="$1"
    local expected_state="$2"
    local timeout="${3:-30}"
    
    local elapsed=0
    while ((elapsed < timeout)); do
        local state=$(container_state "$name")
        if [[ "$state" == "$expected_state" ]]; then
            return 0
        fi
        sleep 1
        ((elapsed++))
    done
    
    echo "Timeout waiting for container $name to reach state $expected_state (current: $state)"
    return 1
}

# Force cleanup a container (ignore errors)
# Usage: cleanup_container NAME
cleanup_container() {
    local name="$1"
    ssh_vm "incus delete '$name' --force" &>/dev/null || true
}

# ============================================================================
# D-Bus Helpers
# ============================================================================

# Call a D-Bus method on the daemon
# Usage: dbus_call METHOD [ARGS...]
dbus_call() {
    local method="$1"
    shift
    ssh_vm "busctl call org.kde.kapsule /org/kde/kapsule org.kde.kapsule.Manager '$method' $*"
}

# Get a D-Bus property
# Usage: dbus_get_property PROPERTY
dbus_get_property() {
    local property="$1"
    ssh_vm "busctl get-property org.kde.kapsule /org/kde/kapsule org.kde.kapsule.Manager '$property'"
}

# ============================================================================
# Assertion Helpers
# ============================================================================

# Assert that a command succeeds
# Usage: assert_success DESCRIPTION COMMAND...
assert_success() {
    local description="$1"
    shift
    
    if "$@"; then
        echo -e "  ${GREEN}✓${NC} $description"
        return 0
    else
        echo -e "  ${RED}✗${NC} $description"
        echo "    Command failed: $*"
        return 1
    fi
}

# Assert that a command fails
# Usage: assert_failure DESCRIPTION COMMAND...
assert_failure() {
    local description="$1"
    shift
    
    if ! "$@"; then
        echo -e "  ${GREEN}✓${NC} $description"
        return 0
    else
        echo -e "  ${RED}✗${NC} $description (expected failure)"
        return 1
    fi
}

# Assert string equality
# Usage: assert_eq DESCRIPTION EXPECTED ACTUAL
assert_eq() {
    local description="$1"
    local expected="$2"
    local actual="$3"
    
    if [[ "$expected" == "$actual" ]]; then
        echo -e "  ${GREEN}✓${NC} $description"
        return 0
    else
        echo -e "  ${RED}✗${NC} $description"
        echo "    Expected: $expected"
        echo "    Actual:   $actual"
        return 1
    fi
}

# Assert string contains substring
# Usage: assert_contains DESCRIPTION HAYSTACK NEEDLE
assert_contains() {
    local description="$1"
    local haystack="$2"
    local needle="$3"
    
    if [[ "$haystack" == *"$needle"* ]]; then
        echo -e "  ${GREEN}✓${NC} $description"
        return 0
    else
        echo -e "  ${RED}✗${NC} $description"
        echo "    String does not contain: $needle"
        return 1
    fi
}

# Assert container exists
# Usage: assert_container_exists NAME
assert_container_exists() {
    local name="$1"
    
    if container_exists "$name"; then
        echo -e "  ${GREEN}✓${NC} Container '$name' exists"
        return 0
    else
        echo -e "  ${RED}✗${NC} Container '$name' does not exist"
        return 1
    fi
}

# Assert container does not exist
# Usage: assert_container_not_exists NAME
assert_container_not_exists() {
    local name="$1"
    
    if ! container_exists "$name"; then
        echo -e "  ${GREEN}✓${NC} Container '$name' does not exist"
        return 0
    else
        echo -e "  ${RED}✗${NC} Container '$name' exists (expected not to)"
        return 1
    fi
}

# Assert container state
# Usage: assert_container_state NAME EXPECTED_STATE
assert_container_state() {
    local name="$1"
    local expected="$2"
    local actual=$(container_state "$name")
    
    if [[ "$actual" == "$expected" ]]; then
        echo -e "  ${GREEN}✓${NC} Container '$name' is $expected"
        return 0
    else
        echo -e "  ${RED}✗${NC} Container '$name' state mismatch"
        echo "    Expected: $expected"
        echo "    Actual:   $actual"
        return 1
    fi
}
