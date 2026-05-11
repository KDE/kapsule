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

# Refresh outcome is reported as one of three summary lines depending on
# whether incus actually found a new version upstream:
#   "Refreshed N/N image(s)"           -- all images downloaded fresh
#   "All N image(s) already up to date" -- nothing changed (cache hit
#                                          or server has no new build)
#   "Of N image(s): refreshed X, ..."  -- mixed outcomes
#
# Whether a given test run hits the refresh path or the no-op path
# depends on simplestreams cache state and what CI has published since
# we last looked, so assertions must accept both. We grep for any of
# the three.
assert_refresh_summary() {
    local description="$1"
    local output="$2"
    local expected_count="$3"

    if [[ "$output" == *"Refreshed ${expected_count}/${expected_count} image(s)"* ]] \
        || [[ "$output" == *"All ${expected_count} image(s) already up to date"* ]] \
        || [[ "$output" == *"Of ${expected_count} image(s):"* ]]; then
        echo -e "  ${GREEN}✓${NC} ${description}"
        return 0
    else
        echo -e "  ${RED}✗${NC} ${description}"
        echo "    Expected one of:"
        echo "      Refreshed ${expected_count}/${expected_count} image(s)"
        echo "      All ${expected_count} image(s) already up to date"
        echo "      Of ${expected_count} image(s): ..."
        return 1
    fi
}

# --- Test 1: Refresh all cached images ---
echo ""
echo "Test: Refresh all cached images (no argument)"
output=$(ssh_vm "kapsule image refresh" 2>&1)
assert_success "Command exits successfully" test $? -eq 0
assert_contains "Shows refreshing header" "$output" "Refreshing all cached images"
assert_contains "Reports images found" "$output" "image(s) to refresh"
# Test 1 doesn't know the count, so just look for any summary verb.
[[ "$output" == *"Refreshed "* ]] || [[ "$output" == *"already up to date"* ]] || [[ "$output" == *"Of "* ]]
assert_success "Reports refresh summary" test $? -eq 0

# --- Test 2: Refresh with server:alias filter ---
echo ""
echo "Test: Refresh with server:alias filter (kapsule:archlinux)"
output=$(ssh_vm "kapsule image refresh kapsule:archlinux" 2>&1)
assert_success "Command exits successfully" test $? -eq 0
assert_contains "Shows refreshing header" "$output" "Refreshing image: kapsule:archlinux"
assert_contains "Finds exactly 1 image" "$output" "Found 1 image(s) to refresh"
assert_contains "Targets kapsule server" "$output" "storage.kde.org/ci-artifacts/kde-linux/kapsule"
assert_refresh_summary "Reports a refresh outcome for the 1 image" "$output" 1

# --- Test 3: Refresh with bare alias (matches multiple servers) ---
echo ""
echo "Test: Refresh with bare alias (archlinux)"
output=$(ssh_vm "kapsule image refresh archlinux" 2>&1)
assert_success "Command exits successfully" test $? -eq 0
assert_contains "Shows refreshing header" "$output" "Refreshing image: archlinux"
assert_contains "Finds 2 images" "$output" "Found 2 image(s) to refresh"
assert_refresh_summary "Reports a refresh outcome for the 2 images" "$output" 2

# --- Test 4: Invalid server alias produces error ---
echo ""
echo "Test: Invalid server alias returns error"
output=$(ssh_vm "kapsule image refresh bogus:nonexistent" 2>&1) && exit_code=0 || exit_code=$?
assert_failure "Command exits with non-zero status" test "$exit_code" -eq 0
assert_contains "Shows error about unknown alias" "$output" "Unknown server alias"
assert_contains "Lists known aliases" "$output" "Known aliases"

echo ""
echo "All image refresh tests completed."
