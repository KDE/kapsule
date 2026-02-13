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
# This ensures runtime mounts are set up properly
kapsule_exec() {
    timeout --kill-after=2 20 ssh $SSH_OPTS "$TEST_VM" "kapsule enter '$CONTAINER_NAME' -- $*"
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

set -x
ready=false
for i in $(seq 1 30); do
    echo "  [debug] readiness probe ${i}/30"
    if probe_output=$(timeout --kill-after=2 8 ssh $SSH_OPTS "$TEST_VM" "incus exec '$CONTAINER_NAME' -- true" 2>&1); then
        ready=true
        echo -e "  ${GREEN}✓${NC} Container base runtime is ready"
        break
    fi

    probe_exit=$?
    echo "  waiting for kapsule enter readiness ($i/30)... (exit=$probe_exit)"
    if [[ -n "$probe_output" ]]; then
        echo "$probe_output" | sed 's/^/    /'
    fi
    sleep 1
done
set +x

if [[ "$ready" != "true" ]]; then
    echo -e "  ${RED}✗${NC} Container did not become enter-ready in time"
    echo "  Incus state:"
    ssh_vm "incus info '$CONTAINER_NAME' 2>/dev/null | sed 's/^/    /'" || true
    echo "  Last 30 daemon log lines:"
    ssh_vm "journalctl -u kapsule-daemon.service --no-pager -n 30 2>/dev/null | sed 's/^/    /'" || true
    exit 1
fi

# Get the test user's UID on the VM
set -x
if [[ "$TEST_VM" == "localhost" || "$TEST_VM" == "127.0.0.1" ]]; then
    uid=$(id -u) || {
        echo -e "  ${RED}✗${NC} Failed to determine local UID"
        exit 1
    }
else
    uid=$(ssh_vm "id -u") || {
        echo -e "  ${RED}✗${NC} Failed to determine host UID over SSH"
        exit 1
    }
fi
set +x

# Test: Check that runtime directory sockets exist (using kapsule enter to set them up)
echo ""
echo "3. Checking runtime audio sockets"

# These are the critical tests - verify host sockets are exposed in-container

# Check PipeWire socket passthrough
echo ""
echo "   Checking PipeWire socket..."
if ssh_vm "test -S /run/user/$uid/pipewire-0" 2>/dev/null; then
    if kapsule_exec "test -S /run/user/$uid/pipewire-0" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} PipeWire socket is available in container"
    else
        echo -e "  ${RED}✗${NC} PipeWire socket missing in container (host has PipeWire)"
        exit 1
    fi
else
    echo -e "  ${YELLOW}!${NC} PipeWire socket not found on host (skipping passthrough check)"
fi

# Check PulseAudio socket passthrough
echo ""
echo "   Checking PulseAudio socket..."
if ssh_vm "test -S /run/user/$uid/pulse/native" 2>/dev/null; then
    if kapsule_exec "test -d /run/user/$uid/pulse" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} PulseAudio directory exists"
    else
        echo -e "  ${RED}✗${NC} PulseAudio directory missing in container (host has PulseAudio)"
        exit 1
    fi

    if kapsule_exec "test -S /run/user/$uid/pulse/native" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} PulseAudio native socket is available in container"
    else
        echo -e "  ${RED}✗${NC} PulseAudio native socket missing in container (host has PulseAudio)"
        exit 1
    fi
else
    echo -e "  ${YELLOW}!${NC} PulseAudio native socket not found on host (skipping passthrough check)"
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
