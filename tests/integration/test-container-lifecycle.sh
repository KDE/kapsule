#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: Container lifecycle operations (create, start, stop, delete)
#
# Tests the basic container lifecycle through the kapsule CLI.

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_NAME="test-lifecycle"

# ============================================================================
# Setup
# ============================================================================

cleanup_container "$CONTAINER_NAME"

# ============================================================================
# Tests
# ============================================================================

echo "Testing container lifecycle..."

# Test: Create container
echo ""
echo "1. Create container"
output=$(create_container "$CONTAINER_NAME" "images:alpine/edge" 2>&1) || {
    echo "Create failed with output:"
    echo "$output"
    exit 1
}
assert_container_exists "$CONTAINER_NAME"
assert_container_state "$CONTAINER_NAME" "RUNNING"

# Test: Stop container
echo ""
echo "2. Stop container"
ssh_vm "kapsule stop '$CONTAINER_NAME'" || {
    echo "Stop failed"
    exit 1
}
wait_for_state "$CONTAINER_NAME" "STOPPED" 30
assert_container_state "$CONTAINER_NAME" "STOPPED"

# Test: Start container
echo ""
echo "3. Start container"
ssh_vm "kapsule start '$CONTAINER_NAME'" || {
    echo "Start failed"
    exit 1
}
wait_for_state "$CONTAINER_NAME" "RUNNING" 30
assert_container_state "$CONTAINER_NAME" "RUNNING"

# Test: Delete running container (should fail without --force)
echo ""
echo "4. Delete running container (expect failure without --force)"
if ssh_vm "kapsule rm '$CONTAINER_NAME'" 2>/dev/null; then
    echo -e "  ${RED}✗${NC} Delete should have failed for running container"
    exit 1
else
    echo -e "  ${GREEN}✓${NC} Delete correctly rejected for running container"
fi

# Test: Force delete
echo ""
echo "5. Force delete container"
delete_container "$CONTAINER_NAME" --force || {
    echo "Force delete failed"
    exit 1
}
# Give it a moment
sleep 2
assert_container_not_exists "$CONTAINER_NAME"

# ============================================================================
# Cleanup
# ============================================================================

cleanup_container "$CONTAINER_NAME"

echo ""
echo "Container lifecycle tests passed!"
