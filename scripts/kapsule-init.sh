#!/bin/bash
# SPDX-FileCopyrightText: 2024-2026 KDE Community
# SPDX-License-Identifier: BSD-3-Clause
#
# kapsule-init.sh - Initialize kapsule by enabling and starting incus sockets
#
# This script must be run as root (sudo kapsule init).

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info() {
    echo -e "${BOLD}→${NC} $1"
}

success() {
    echo -e "  ${GREEN}✓${NC} $1"
}

failure() {
    echo -e "  ${RED}✗${NC} $1"
}

warning() {
    echo -e "  ${YELLOW}⚠${NC} $1"
}

dim() {
    echo -e "  ${DIM}$1${NC}"
}

# Check for root
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error:${NC} This command must be run as root."
    echo -e "${DIM}Run: ${BOLD}sudo kapsule init${NC}"
    exit 1
fi

echo -e "${BOLD}Initializing kapsule...${NC}"
echo

# Reload systemd to pick up new unit files
info "Reloading systemd daemon..."
if systemctl daemon-reload 2>/dev/null; then
    success "systemd daemon reloaded"
else
    failure "Failed to reload systemd daemon"
    exit 1
fi

# Reload D-Bus to pick up new policy files
info "Reloading D-Bus configuration..."
if systemctl reload dbus 2>/dev/null; then
    success "D-Bus configuration reloaded"
else
    warning "Failed to reload D-Bus (may need reboot)"
fi

# Load kernel modules
info "Loading kernel modules for nested container support..."
if systemctl restart systemd-modules-load 2>/dev/null; then
    success "Kernel modules loaded"
else
    warning "Failed to load kernel modules"
fi

# Run systemd-sysusers
info "Running systemd-sysusers..."
if systemd-sysusers 2>/dev/null; then
    success "systemd-sysusers completed"
else
    failure "Failed to run systemd-sysusers"
    exit 1
fi

# Enable and start incus sockets
for unit in incus.socket incus-user.socket; do
    info "Enabling and starting ${unit}..."
    if systemctl enable --now "${unit}" 2>/dev/null; then
        success "${unit} enabled and started"
    else
        failure "Failed to enable ${unit}"
        exit 1
    fi
done

# Wait briefly for incus socket to be ready
sleep 1

# Check if Incus is already initialized
info "Initializing Incus..."
if incus storage show default &>/dev/null; then
    dim "Incus already initialized (storage pool 'default' exists)"
else
    # Use preseed to initialize Incus with default configuration
    if incus admin init --preseed <<'EOF'
config: {}
networks: []
storage_pools:
- config:
    source: /var/lib/incus/storage-pools/default
  description: ""
  name: default
  driver: btrfs
storage_volumes: []
profiles:
- config: {}
  description: ""
  devices:
    root:
      path: /
      pool: default
      type: disk
  name: default
  project: default
projects: []
certificates: []
cluster_groups: []
cluster: null
EOF
    then
        success "Incus initialized with default storage pool and profile"
    else
        failure "Failed to initialize Incus"
        exit 1
    fi
fi

# Disable automatic image updates
info "Configuring Incus settings..."
if incus config set images.auto_update_interval 0 2>/dev/null; then
    success "Disabled automatic image updates"
else
    warning "Failed to disable automatic image updates"
fi

echo
echo -e "${GREEN}${BOLD}✓ Kapsule initialized successfully!${NC}"
echo -e "${DIM}You can now use kapsule commands as a regular user.${NC}"
