#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Integration tests for host config sync (timezone, locale, DNS)
#
# Tests that:
# 1. Sync scripts exist in the container image
# 2. Timezone is synced at container creation time
# 3. Locale is synced at container creation time
# 4. DNS is synced at container creation time
# 5. Timezone change on host propagates to running container
# 6. Locale change on host propagates to running container

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER="test-host-config-sync"
# Use the kapsule image that includes sync scripts
IMAGE="kapsule:archlinux"

# ============================================================================
# Setup / Teardown
# ============================================================================

cleanup_container "$CONTAINER"
trap 'cleanup_container "$CONTAINER"' EXIT

# ============================================================================
# Helpers
# ============================================================================

ssh_vm_root() {
    ssh $SSH_OPTS "root@${TEST_VM#*@}" "$@"
}

exec_in_container() {
    ssh_vm "incus exec '$CONTAINER' -- $*"
}

get_host_timezone() {
    ssh_vm "busctl get-property org.freedesktop.timedate1 \
        /org/freedesktop/timedate1 org.freedesktop.timedate1 Timezone" \
        | sed 's/^s "\(.*\)"$/\1/'
}

get_host_locale() {
    ssh_vm "cat /etc/locale.conf"
}

set_host_timezone() {
    ssh_vm_root "timedatectl set-timezone '$1'"
}

set_host_locale() {
    ssh_vm_root "localectl set-locale '$1'"
}

# ============================================================================
# Tests
# ============================================================================

echo "Host Config Sync Tests"
echo "======================"

# Save the host's current timezone and locale so we can restore them
ORIG_TZ=$(get_host_timezone)
ORIG_LOCALE=$(get_host_locale)
echo "  Host timezone: $ORIG_TZ"
echo "  Host locale:   $ORIG_LOCALE"

restore_host_config() {
    set_host_timezone "$ORIG_TZ" 2>/dev/null || true
    # localectl set-locale expects KEY=VALUE format
    set_host_locale "$ORIG_LOCALE" 2>/dev/null || true
    cleanup_container "$CONTAINER"
}
trap restore_host_config EXIT

# --- Create the container ---
echo ""
echo "Creating container from $IMAGE..."
create_container "$CONTAINER" "$IMAGE"
wait_for_state "$CONTAINER" "RUNNING" 30

# --- Test 1: Sync scripts exist ---
echo ""
echo "Test: Sync scripts exist in container"
assert_success "/.kapsule/sync/dns exists and is executable" \
    exec_in_container test -x /.kapsule/sync/dns
assert_success "/.kapsule/sync/timezone exists and is executable" \
    exec_in_container test -x /.kapsule/sync/timezone
assert_success "/.kapsule/sync/locale exists and is executable" \
    exec_in_container test -x /.kapsule/sync/locale

# --- Test 2: Timezone synced at creation ---
echo ""
echo "Test: Timezone synced at creation time"
container_tz=$(exec_in_container cat /etc/timezone 2>/dev/null | tr -d '\r\n')
assert_eq "Container timezone matches host" "$ORIG_TZ" "$container_tz"

container_localtime=$(exec_in_container readlink /etc/localtime 2>/dev/null | tr -d '\r\n')
assert_eq "Container /etc/localtime symlink correct" \
    "/usr/share/zoneinfo/$ORIG_TZ" "$container_localtime"

# --- Test 3: Locale synced at creation ---
echo ""
echo "Test: Locale synced at creation time"
container_locale=$(exec_in_container cat /etc/locale.conf 2>/dev/null | tr -d '\r')
assert_eq "Container locale.conf matches host" "$ORIG_LOCALE" "$container_locale"

# --- Test 4: DNS synced at creation ---
echo ""
echo "Test: DNS synced at creation time"
host_resolv=$(ssh_vm "cat /etc/resolv.conf")
container_resolv=$(exec_in_container cat /etc/resolv.conf 2>/dev/null | tr -d '\r')
assert_eq "Container resolv.conf matches host" "$host_resolv" "$container_resolv"

# --- Test 5: Timezone change propagates ---
echo ""
echo "Test: Timezone change propagates to running container"
NEW_TZ="Pacific/Auckland"
set_host_timezone "$NEW_TZ"

# Wait for the D-Bus signal to propagate and sync script to run in all containers
sleep 5

container_tz=$(exec_in_container cat /etc/timezone 2>/dev/null | tr -d '\r\n')
assert_eq "Container timezone updated after host change" "$NEW_TZ" "$container_tz"

container_localtime=$(exec_in_container readlink /etc/localtime 2>/dev/null | tr -d '\r\n')
assert_eq "Container /etc/localtime symlink updated" \
    "/usr/share/zoneinfo/$NEW_TZ" "$container_localtime"

# Restore original timezone
set_host_timezone "$ORIG_TZ"

# --- Test 6: Locale change propagates ---
echo ""
echo "Test: Locale change propagates to running container"
NEW_LOCALE="de_DE.UTF-8"
set_host_locale "LANG=$NEW_LOCALE"

# Wait for the D-Bus signal to propagate and sync script to run in all containers
# (locale-gen can take several seconds, and all containers are synced sequentially)
sleep 5

container_lang=$(exec_in_container cat /etc/locale.conf 2>/dev/null | grep '^LANG=' | tr -d '\r\n')
assert_eq "Container LANG updated after host change" "LANG=$NEW_LOCALE" "$container_lang"

# Verify the locale was actually generated
locale_gen=$(exec_in_container cat /etc/locale.gen 2>/dev/null | tr -d '\r')
assert_contains "locale.gen contains new locale" "$locale_gen" "$NEW_LOCALE"

# Restore original locale
set_host_locale "$ORIG_LOCALE"

echo ""
echo "All host config sync tests completed."
