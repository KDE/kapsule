#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Integration tests for the image refresh command
#
# Tests that:
# 1. Refreshing all cached images works (no argument)
# 2. Refreshing with server:alias filter works
# 3. Refreshing with bare alias matches across servers
# 4. Invalid server alias produces an error

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

# ============================================================================
# Tests
# ============================================================================

echo "Image Refresh Tests"
echo "==================="

# --- Test 1: Refresh all cached images ---
echo ""
echo "Test: Refresh all cached images (no argument)"
output=$(ssh_vm "kapsule image refresh" 2>&1)
assert_success "Command exits successfully" test $? -eq 0
assert_contains "Shows refreshing header" "$output" "Refreshing all cached images"
assert_contains "Reports images found" "$output" "image(s) to refresh"
assert_contains "Reports refresh summary" "$output" "Refreshed"

# --- Test 2: Refresh with server:alias filter ---
echo ""
echo "Test: Refresh with server:alias filter (kapsule:archlinux)"
output=$(ssh_vm "kapsule image refresh kapsule:archlinux" 2>&1)
assert_success "Command exits successfully" test $? -eq 0
assert_contains "Shows refreshing header" "$output" "Refreshing image: kapsule:archlinux"
assert_contains "Finds exactly 1 image" "$output" "Found 1 image(s) to refresh"
assert_contains "Refreshes from kapsule server" "$output" "storage.kde.org/ci-artifacts/kde-linux/kapsule"
assert_contains "Reports success" "$output" "Refreshed 1/1 image(s)"

# --- Test 3: Refresh with bare alias (matches multiple servers) ---
echo ""
echo "Test: Refresh with bare alias (archlinux)"
output=$(ssh_vm "kapsule image refresh archlinux" 2>&1)
assert_success "Command exits successfully" test $? -eq 0
assert_contains "Shows refreshing header" "$output" "Refreshing image: archlinux"
assert_contains "Finds 2 images" "$output" "Found 2 image(s) to refresh"
assert_contains "Reports success" "$output" "Refreshed 2/2 image(s)"

# --- Test 4: Invalid server alias produces error ---
echo ""
echo "Test: Invalid server alias returns error"
output=$(ssh_vm "kapsule image refresh bogus:nonexistent" 2>&1) && exit_code=0 || exit_code=$?
assert_failure "Command exits with non-zero status" test "$exit_code" -eq 0
assert_contains "Shows error about unknown alias" "$output" "Unknown server alias"
assert_contains "Lists known aliases" "$output" "Known aliases"

echo ""
echo "All image refresh tests completed."
