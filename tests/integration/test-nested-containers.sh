#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: Nested containers (Docker-in-Kapsule)
#
# Verifies that nested container support works by:
#   1. Creating a Kapsule container
#   2. Installing Docker inside it
#   3. Running docker hello-world to confirm nesting works

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_NAME="test-nesting"

# Helper to run commands in the container via kapsule enter
kapsule_exec() {
    local name="$1"
    shift
    ssh_vm "kapsule enter '$name' -- $*"
}

# ============================================================================
# Setup
# ============================================================================

cleanup_container "$CONTAINER_NAME"

echo "Testing nested containers (Docker-in-Kapsule)..."

# ============================================================================
# 1. Create container
# ============================================================================

echo ""
echo "1. Create container"
output=$(create_container "$CONTAINER_NAME" "images:archlinux" 2>&1) || {
    echo "Create failed with output:"
    echo "$output"
    exit 1
}
assert_container_exists "$CONTAINER_NAME"
assert_container_state "$CONTAINER_NAME" "RUNNING"

# Give the container a moment to fully initialise
sleep 3

# Trigger user setup
kapsule_exec "$CONTAINER_NAME" "true" 2>/dev/null

# ============================================================================
# 2. Install Docker inside the container
# ============================================================================

echo ""
echo "2. Install Docker inside container"
kapsule_exec "$CONTAINER_NAME" "sudo pacman -Sy --noconfirm docker" || {
    echo "Docker installation failed"
    cleanup_container "$CONTAINER_NAME"
    exit 1
}
echo -e "  ${GREEN}✓${NC} Docker installed"

# ============================================================================
# 3. Start Docker daemon
# ============================================================================

echo ""
echo "3. Start Docker daemon"
kapsule_exec "$CONTAINER_NAME" "sudo systemctl start docker" || {
    echo "Failed to start Docker daemon"
    cleanup_container "$CONTAINER_NAME"
    exit 1
}

# Wait for Docker to be ready
sleep 3

kapsule_exec "$CONTAINER_NAME" "sudo docker info" &>/dev/null || {
    echo "Docker daemon not ready"
    cleanup_container "$CONTAINER_NAME"
    exit 1
}
echo -e "  ${GREEN}✓${NC} Docker daemon running"

# ============================================================================
# 4. Run hello-world container
# ============================================================================

echo ""
echo "4. Run Docker hello-world"
hello_output=$(kapsule_exec "$CONTAINER_NAME" "sudo docker run --rm hello-world" 2>&1) || {
    echo "Docker hello-world failed:"
    echo "$hello_output"
    cleanup_container "$CONTAINER_NAME"
    exit 1
}

assert_contains "hello-world output contains greeting" "$hello_output" "Hello from Docker!"

# ============================================================================
# Cleanup
# ============================================================================

cleanup_container "$CONTAINER_NAME"

echo ""
echo "Nested container tests passed!"
