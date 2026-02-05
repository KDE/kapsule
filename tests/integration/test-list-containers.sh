#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: List containers operation
#
# Tests the container listing functionality.

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_1="test-list-1"
CONTAINER_2="test-list-2"

# ============================================================================
# Setup
# ============================================================================

cleanup_container "$CONTAINER_1"
cleanup_container "$CONTAINER_2"

# ============================================================================
# Tests
# ============================================================================

echo "Testing list containers..."

# Test: List with no containers (or existing ones)
echo ""
echo "1. List containers (baseline)"
baseline_output=$(ssh_vm "kapsule list" 2>&1)
echo "   Baseline output captured"

# Test: Create containers and verify they appear in list
echo ""
echo "2. Create test containers"
create_container "$CONTAINER_1" "images:alpine/edge" >/dev/null 2>&1
assert_container_exists "$CONTAINER_1"

create_container "$CONTAINER_2" "images:alpine/edge" >/dev/null 2>&1
assert_container_exists "$CONTAINER_2"

# Test: List shows both containers
echo ""
echo "3. Verify containers in list"
list_output=$(ssh_vm "kapsule list" 2>&1)

assert_contains "Container 1 in list" "$list_output" "$CONTAINER_1"
assert_contains "Container 2 in list" "$list_output" "$CONTAINER_2"

# Test: List via D-Bus directly
echo ""
echo "4. List via D-Bus"
dbus_output=$(dbus_call "ListContainers" 2>&1) || {
    echo "D-Bus ListContainers failed"
    exit 1
}
assert_contains "D-Bus shows container 1" "$dbus_output" "$CONTAINER_1"
assert_contains "D-Bus shows container 2" "$dbus_output" "$CONTAINER_2"

# ============================================================================
# Cleanup
# ============================================================================

cleanup_container "$CONTAINER_1"
cleanup_container "$CONTAINER_2"

echo ""
echo "List containers tests passed!"
