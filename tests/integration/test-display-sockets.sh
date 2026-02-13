#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: Display socket passthrough (X11 and Wayland)
#
# Tests that X11 and Wayland sockets are correctly exposed
# in containers and that graphical applications can connect to the
# host's display server.

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_NAME="test-display-sockets"

# Helper to run commands in container via kapsule enter
# Exports DISPLAY, WAYLAND_DISPLAY, and XAUTHORITY so the daemon (which reads
# /proc/<pid>/environ) sets up the correct runtime mounts.
kapsule_exec() {
    ssh_vm "DISPLAY=$HOST_DISPLAY WAYLAND_DISPLAY=$HOST_WAYLAND XAUTHORITY=$HOST_XAUTHORITY kapsule enter '$CONTAINER_NAME' -- $*"
}

# ============================================================================
# Setup
# ============================================================================

cleanup_container "$CONTAINER_NAME"

# Display env vars are not set over SSH, so use the known defaults
# for a typical KDE Plasma session on the VM
HOST_DISPLAY=":0"
HOST_WAYLAND="wayland-0"
HOST_UID=$(ssh_vm "id -u")
display_num="${HOST_DISPLAY#:}"
display_num="${display_num%%.*}"  # strip screen number if present

# XAUTHORITY has a random filename — discover it from the running session
HOST_XAUTHORITY=$(ssh_vm "cat /proc/\$(pgrep -u \$(id -u) plasmashell | head -1)/environ 2>/dev/null | tr '\0' '\n' | grep ^XAUTHORITY= | cut -d= -f2")

if ssh_vm "test -S /tmp/.X11-unix/X${display_num}" 2>/dev/null; then
    HOST_HAS_X11_SOCKET=true
else
    HOST_HAS_X11_SOCKET=false
fi

if ssh_vm "test -S /run/user/$HOST_UID/$HOST_WAYLAND" 2>/dev/null; then
    HOST_HAS_WAYLAND_SOCKET=true
else
    HOST_HAS_WAYLAND_SOCKET=false
fi

echo "Host environment:"
echo "  DISPLAY=$HOST_DISPLAY"
echo "  WAYLAND_DISPLAY=$HOST_WAYLAND"
echo "  XAUTHORITY=$HOST_XAUTHORITY"
echo "  UID=$HOST_UID"

# ============================================================================
# Tests
# ============================================================================

echo ""
echo "Testing display socket passthrough..."

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

# ============================================================================
# X11 socket tests
# ============================================================================

echo ""
echo "3. Checking X11 socket passthrough"

# The X11 socket directory is created at enter time and the individual
# socket should be available inside the container.
if kapsule_exec "test -d /tmp/.X11-unix" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} /tmp/.X11-unix directory exists"
else
    echo -e "  ${RED}✗${NC} /tmp/.X11-unix directory missing"
    exit 1
fi

if [[ "$HOST_HAS_X11_SOCKET" == true ]]; then
    if kapsule_exec "test -S /tmp/.X11-unix/X${display_num}" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} X11 socket X${display_num} is available in container"
    else
        echo -e "  ${RED}✗${NC} X11 socket X${display_num} missing in container (host has X11 socket)"
        exit 1
    fi
else
    echo -e "  ${YELLOW}!${NC} Host X11 socket X${display_num} not found, skipping direct socket presence check"
fi

# Check XAUTHORITY availability
echo ""
echo "4. Checking XAUTHORITY availability"
if [[ -n "$HOST_XAUTHORITY" ]]; then
    xauth_basename=$(basename "$HOST_XAUTHORITY")
    if ssh_vm "test -f '$HOST_XAUTHORITY'" 2>/dev/null; then
        if kapsule_exec "test -r /run/user/$HOST_UID/$xauth_basename" 2>/dev/null; then
            echo -e "  ${GREEN}✓${NC} XAUTHORITY file is readable in container"
        else
            echo -e "  ${RED}✗${NC} XAUTHORITY file not readable in container for $xauth_basename"
            exit 1
        fi
    else
        echo -e "  ${YELLOW}!${NC} Host XAUTHORITY file not found, skipping"
    fi
else
    echo -e "  ${YELLOW}!${NC} XAUTHORITY not discovered from host session, skipping"
fi

# ============================================================================
# Wayland socket tests
# ============================================================================

echo ""
echo "5. Checking Wayland socket passthrough"

if [[ -n "$HOST_WAYLAND" ]]; then
    if [[ "$HOST_HAS_WAYLAND_SOCKET" == true ]]; then
        if kapsule_exec "test -S /run/user/$HOST_UID/$HOST_WAYLAND" 2>/dev/null; then
            echo -e "  ${GREEN}✓${NC} Wayland socket is available in container"
        else
            echo -e "  ${RED}✗${NC} Wayland socket missing in container for $HOST_WAYLAND"
            exit 1
        fi
    else
        echo -e "  ${YELLOW}!${NC} Host Wayland socket $HOST_WAYLAND not found, skipping direct socket presence check"
    fi
else
    echo -e "  ${YELLOW}!${NC} WAYLAND_DISPLAY not set on host, skipping Wayland socket check"
fi

# Check host socket accessible through hostfs
echo ""
echo "6. Checking host socket accessibility through hostfs"

if [[ -n "$HOST_WAYLAND" ]]; then
    if [[ "$HOST_HAS_WAYLAND_SOCKET" == true ]]; then
        if kapsule_exec "test -S /.kapsule/host/run/user/$HOST_UID/$HOST_WAYLAND" 2>/dev/null; then
            echo -e "  ${GREEN}✓${NC} Host Wayland socket accessible via hostfs"
        else
            echo -e "  ${RED}✗${NC} Host Wayland socket not accessible via hostfs"
            exit 1
        fi
    else
        echo -e "  ${YELLOW}!${NC} Host Wayland socket not present, skipping hostfs accessibility check"
    fi
fi

if kapsule_exec "test -d /.kapsule/host/tmp/.X11-unix" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Host X11 socket directory accessible via hostfs"
else
    echo -e "  ${YELLOW}!${NC} Host X11 socket directory not accessible via hostfs"
fi

# ============================================================================
# Install display tools
# ============================================================================

echo ""
echo "7. Installing display tools..."
# xorg-xdpyinfo: validates X11 connection (tiny, libX11 only)
# xorg-xmessage: lightest X11 app with a real window + built-in timeout
# wayland-utils: wayland-info validates Wayland connection (libwayland-client only)
# foot: lightest Wayland-native terminal, can run a command and exit
# ttf-dejavu: monospace font required by foot
# mesa-utils: glxinfo/eglinfo for GPU validation
kapsule_exec "sudo pacman -Syu --noconfirm xorg-xdpyinfo xorg-xmessage wayland-utils foot ttf-dejavu mesa-utils" &>/dev/null || {
    echo -e "  ${YELLOW}!${NC} Failed to install some display packages"
}

# ============================================================================
# X11 connection tests
# ============================================================================

echo ""
echo "8. Testing X11 connection with xdpyinfo"
if [[ -n "$HOST_DISPLAY" && "$HOST_HAS_X11_SOCKET" == true ]]; then
    xdpyinfo_output=$(kapsule_exec "xdpyinfo" 2>&1)
    xdpyinfo_exit=$?
    if [[ $xdpyinfo_exit -eq 0 ]]; then
        echo -e "  ${GREEN}✓${NC} xdpyinfo succeeded"
        screen_line=$(echo "$xdpyinfo_output" | grep "dimensions:" | head -1)
        if [[ -n "$screen_line" ]]; then
            echo "    $screen_line"
        fi
    else
        echo -e "  ${RED}✗${NC} xdpyinfo failed"
        echo "    $xdpyinfo_output"
        exit 1
    fi
else
    echo -e "  ${YELLOW}!${NC} Skipping X11 connection test (host X11 socket unavailable)"
fi

echo ""
echo "9. Testing X11 window creation with xmessage"
if [[ -n "$HOST_DISPLAY" && "$HOST_HAS_X11_SOCKET" == true ]]; then
    # xmessage -timeout N exits after N seconds with code 0
    xmessage_output=$(kapsule_exec "xmessage -timeout 3 'Kapsule X11 test'" 2>&1)
    xmessage_exit=$?
    if [[ $xmessage_exit -eq 0 ]]; then
        echo -e "  ${GREEN}✓${NC} xmessage window created and exited cleanly"
    else
        echo -e "  ${RED}✗${NC} xmessage failed (exit $xmessage_exit)"
        echo "    $xmessage_output"
        exit 1
    fi
else
    echo -e "  ${YELLOW}!${NC} Skipping X11 window test (host X11 socket unavailable)"
fi

echo ""
echo "10. Testing GPU (X11) with glxinfo"
if [[ -n "$HOST_DISPLAY" && "$HOST_HAS_X11_SOCKET" == true ]]; then
    glxinfo_output=$(kapsule_exec "glxinfo -B" 2>&1)
    glxinfo_exit=$?
    if [[ $glxinfo_exit -eq 0 ]]; then
        echo -e "  ${GREEN}✓${NC} glxinfo succeeded"
        renderer=$(echo "$glxinfo_output" | grep "OpenGL renderer" | head -1)
        if [[ -n "$renderer" ]]; then
            echo "    $renderer"
        fi
    else
        echo -e "  ${RED}✗${NC} glxinfo failed"
        echo "    $glxinfo_output"
        exit 1
    fi
else
    echo -e "  ${YELLOW}!${NC} Skipping glxinfo test (host X11 socket unavailable)"
fi

# ============================================================================
# Wayland connection tests
# ============================================================================

echo ""
echo "11. Testing Wayland connection with wayland-info"
if [[ -n "$HOST_WAYLAND" && "$HOST_HAS_WAYLAND_SOCKET" == true ]]; then
    wayinfo_output=$(kapsule_exec "wayland-info" 2>&1)
    wayinfo_exit=$?
    if [[ $wayinfo_exit -eq 0 ]]; then
        echo -e "  ${GREEN}✓${NC} wayland-info succeeded"
        compositor=$(echo "$wayinfo_output" | grep -i "compositor" | head -1)
        if [[ -n "$compositor" ]]; then
            echo "    $compositor"
        fi
    else
        echo -e "  ${RED}✗${NC} wayland-info failed"
        echo "    $wayinfo_output"
        exit 1
    fi
else
    echo -e "  ${YELLOW}!${NC} Skipping Wayland connection test (host Wayland socket unavailable)"
fi

echo ""
echo "12. Testing Wayland window creation with foot"
if [[ -n "$HOST_WAYLAND" && "$HOST_HAS_WAYLAND_SOCKET" == true ]]; then
    # foot -e <cmd> opens a terminal, runs the command, and exits
    foot_output=$(kapsule_exec "foot -e true" 2>&1)
    foot_exit=$?
    if [[ $foot_exit -eq 0 ]]; then
        echo -e "  ${GREEN}✓${NC} foot window created and exited cleanly"
    else
        echo -e "  ${RED}✗${NC} foot failed (exit $foot_exit)"
        echo "    $foot_output"
        exit 1
    fi
else
    echo -e "  ${YELLOW}!${NC} Skipping Wayland window test (host Wayland socket unavailable)"
fi

echo ""
echo "13. Testing GPU (Wayland) with eglinfo"
if [[ -n "$HOST_WAYLAND" && "$HOST_HAS_WAYLAND_SOCKET" == true ]]; then
    # eglinfo often exits non-zero when some platforms (GBM, device) fail,
    # even if Wayland/X11 platforms work fine. Use || true to prevent set -e.
    eglinfo_output=$(kapsule_exec "eglinfo" 2>&1) || true
    if echo "$eglinfo_output" | grep -qi "renderer"; then
        echo -e "  ${GREEN}✓${NC} eglinfo found a renderer"
        renderer=$(echo "$eglinfo_output" | grep -i "renderer" | head -1)
        echo "    $renderer"
    else
        echo -e "  ${YELLOW}!${NC} eglinfo could not find a renderer"
    fi
else
    echo -e "  ${YELLOW}!${NC} Skipping eglinfo test (host Wayland socket unavailable)"
fi

# ============================================================================
# Cleanup
# ============================================================================

echo ""
echo "14. Cleanup"
cleanup_container "$CONTAINER_NAME"
assert_container_not_exists "$CONTAINER_NAME"

echo ""
echo "Display socket passthrough tests passed!"
