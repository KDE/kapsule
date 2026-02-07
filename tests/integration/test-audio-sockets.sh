#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: Audio socket passthrough (PulseAudio and PipeWire)
#
# Tests that PulseAudio and PipeWire sockets are correctly mounted
# in containers and that audio tools can connect to the host's
# audio server.

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_NAME="test-audio-sockets"

# Helper to run commands in container via kapsule enter
# This ensures runtime symlinks are set up properly
kapsule_exec() {
    ssh_vm "kapsule enter '$CONTAINER_NAME' -- $*"
}

# ============================================================================
# Setup
# ============================================================================

cleanup_container "$CONTAINER_NAME"

# ============================================================================
# Tests
# ============================================================================

echo "Testing audio socket passthrough..."

# Test: Create container
echo ""
echo "1. Create container"
output=$(create_container "$CONTAINER_NAME" "images:archlinux" 2>&1) || {
    echo "Create failed with output:"
    echo "$output"
    exit 1
}
assert_container_exists "$CONTAINER_NAME"
assert_container_state "$CONTAINER_NAME" "RUNNING"

# Wait for container to fully initialize
echo ""
echo "2. Waiting for container to initialize..."
sleep 3

# Get the test user's UID on the VM
uid=$(ssh_vm "id -u")

# Test: Check that runtime directory sockets exist (using kapsule enter to set them up)
echo ""
echo "3. Checking runtime directory symlinks"

# These are the critical tests - verify symlinks are created

# Check PipeWire socket symlink exists
echo ""
echo "   Checking PipeWire socket..."
if kapsule_exec "test -L /run/user/$uid/pipewire-0" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} PipeWire socket symlink exists"
    
    # Verify symlink target
    target=$(kapsule_exec "readlink /run/user/$uid/pipewire-0" 2>/dev/null)
    expected_target="/.kapsule/host/run/user/$uid/pipewire-0"
    if [[ "$target" == "$expected_target" ]]; then
        echo -e "  ${GREEN}✓${NC} PipeWire symlink points to host socket"
    else
        echo -e "  ${RED}✗${NC} PipeWire symlink has wrong target: $target"
        exit 1
    fi
else
    # This is a failure if PipeWire is running on host
    if ssh_vm "test -S /run/user/$uid/pipewire-0" 2>/dev/null; then
        echo -e "  ${RED}✗${NC} PipeWire socket symlink not created (but host has PipeWire)"
        exit 1
    else
        echo -e "  ${YELLOW}!${NC} PipeWire socket symlink not found (PipeWire not running on host)"
    fi
fi

# Check PulseAudio: pulse/ should be a real directory with native symlink inside
echo ""
echo "   Checking PulseAudio socket..."
if kapsule_exec "test -d /run/user/$uid/pulse" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} PulseAudio directory exists"
    
    # Verify native socket symlink inside pulse/
    if kapsule_exec "test -L /run/user/$uid/pulse/native" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} PulseAudio native socket symlink exists"
        
        target=$(kapsule_exec "readlink /run/user/$uid/pulse/native" 2>/dev/null)
        expected_target="/.kapsule/host/run/user/$uid/pulse/native"
        if [[ "$target" == "$expected_target" ]]; then
            echo -e "  ${GREEN}✓${NC} PulseAudio native symlink points to host socket"
        else
            echo -e "  ${RED}✗${NC} PulseAudio native symlink has wrong target: $target"
            exit 1
        fi
    else
        echo -e "  ${RED}✗${NC} PulseAudio native socket symlink not found inside pulse/"
        exit 1
    fi
else
    # This is a failure if PulseAudio is running on host
    if ssh_vm "test -d /run/user/$uid/pulse" 2>/dev/null; then
        echo -e "  ${RED}✗${NC} PulseAudio directory not created (but host has PulseAudio)"
        exit 1
    else
        echo -e "  ${YELLOW}!${NC} PulseAudio not found (PulseAudio not running on host)"
    fi
fi

# Check that the host sockets are accessible through hostfs
echo ""
echo "4. Checking host socket accessibility through hostfs"

if kapsule_exec "test -S /.kapsule/host/run/user/$uid/pipewire-0" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Host PipeWire socket accessible via hostfs"
else
    echo -e "  ${YELLOW}!${NC} Host PipeWire socket not accessible (may not be running)"
fi

if kapsule_exec "test -d /.kapsule/host/run/user/$uid/pulse" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Host PulseAudio directory accessible via hostfs"
else
    echo -e "  ${YELLOW}!${NC} Host PulseAudio directory not accessible (may not be running)"
fi

# Test: Install and run audio tools
echo ""
echo "5. Installing audio tools (libpulse, pipewire)..."
kapsule_exec "sudo pacman -Syu --noconfirm libpulse pipewire" &>/dev/null || {
    echo -e "  ${YELLOW}!${NC} Failed to install audio packages"
}

echo ""
echo "6. Testing PulseAudio access with pactl"
pactl_output=$(kapsule_exec "pactl info" 2>&1)
pactl_exit=$?
if [[ $pactl_exit -eq 0 ]]; then
    echo -e "  ${GREEN}✓${NC} pactl info succeeded"
    server_name=$(echo "$pactl_output" | grep "Server Name:" | head -1)
    if [[ -n "$server_name" ]]; then
        echo "    $server_name"
    fi
else
    echo -e "  ${RED}✗${NC} pactl info failed"
    echo "    $pactl_output"
    exit 1
fi

echo ""
echo "7. Testing PipeWire access with pw-cli"
pwcli_output=$(kapsule_exec "pw-cli info 0" 2>&1)
pwcli_exit=$?
if [[ $pwcli_exit -eq 0 ]]; then
    echo -e "  ${GREEN}✓${NC} pw-cli info succeeded"
    core_name=$(echo "$pwcli_output" | grep "core.name" | head -1)
    if [[ -n "$core_name" ]]; then
        echo "   $core_name"
    fi
else
    echo -e "  ${RED}✗${NC} pw-cli info failed"
    echo "    $pwcli_output"
    exit 1
fi

# ============================================================================
# Cleanup
# ============================================================================

echo ""
echo "8. Cleanup"
cleanup_container "$CONTAINER_NAME"
assert_container_not_exists "$CONTAINER_NAME"

echo ""
echo "Audio socket passthrough tests passed!"
