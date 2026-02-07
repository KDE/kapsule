#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: Profile sync on daemon startup
#
# Verifies that the kapsule-base Incus profile is created on first
# start, updated when the content hash is stale, and left alone when
# it already matches.

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

PROFILE_NAME="kapsule-base"
HASH_KEY="user.kapsule.profile-hash"

# ============================================================================
# Helpers
# ============================================================================

# Get the value of a config key from the profile
# Usage: get_profile_config KEY
get_profile_config() {
    local key="$1"
    ssh_vm "incus profile get '$PROFILE_NAME' '$key'" 2>/dev/null
}

# Restart the daemon and wait for it to be ready on D-Bus
restart_daemon() {
    ssh $SSH_OPTS "root@$TEST_VM" "systemctl restart kapsule-daemon"

    local retries=10
    while ((retries > 0)); do
        if ssh_vm "busctl status org.kde.kapsule" &>/dev/null; then
            return 0
        fi
        sleep 1
        ((retries--))
    done

    echo "Daemon did not become ready after restart"
    return 1
}

# Get recent daemon journal lines (since last restart)
daemon_journal() {
    # Use the invocation ID to get only logs from the current daemon run
    local invocation
    invocation=$(ssh_vm "systemctl show -p InvocationID --value kapsule-daemon")
    ssh_vm "journalctl _SYSTEMD_INVOCATION_ID=$invocation --no-pager -o cat" 2>/dev/null
}

# ============================================================================
# Tests
# ============================================================================

echo "Testing profile sync..."

# --------------------------------------------------------------------------
# 1. Update path — corrupt the hash, restart, assert it gets fixed
# --------------------------------------------------------------------------
echo ""
echo "1. Profile update on stale hash"

# First make sure the profile exists (it should from the sysext deploy)
if ! ssh_vm "incus profile show '$PROFILE_NAME'" &>/dev/null; then
    echo "Profile does not exist yet, restarting daemon to create it..."
    restart_daemon
fi

# Record the correct hash before corrupting
correct_hash=$(get_profile_config "$HASH_KEY")
if [[ -z "$correct_hash" ]]; then
    echo -e "  ${RED}✗${NC} Profile has no hash key — is the new code deployed?"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Current hash: $correct_hash"

ssh_vm "incus profile set '$PROFILE_NAME' '$HASH_KEY=stale-hash-value'"
stale_hash=$(get_profile_config "$HASH_KEY")
assert_eq "Hash was corrupted" "stale-hash-value" "$stale_hash"

restart_daemon

hash_after_update=$(get_profile_config "$HASH_KEY")
if [[ "$hash_after_update" == "stale-hash-value" ]]; then
    echo -e "  ${RED}✗${NC} Profile hash was not updated (still stale)"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} Profile hash was restored ($hash_after_update)"

assert_eq "Hash matches original" "$correct_hash" "$hash_after_update"

journal=$(daemon_journal)
assert_contains "Journal reports profile updated" "$journal" "Profile 'kapsule-base': updated to match current version"

# --------------------------------------------------------------------------
# 2. No-op path — restart again with a matching profile, assert unchanged
# --------------------------------------------------------------------------
echo ""
echo "2. Profile left alone when hash matches"
hash_before=$(get_profile_config "$HASH_KEY")

restart_daemon

hash_after=$(get_profile_config "$HASH_KEY")
assert_eq "Hash unchanged after no-op restart" "$hash_before" "$hash_after"

# Journal should NOT mention created or updated
journal=$(daemon_journal)
if [[ "$journal" == *"Profile 'kapsule-base': created"* ]] || \
   [[ "$journal" == *"Profile 'kapsule-base': updated to match current version"* ]]; then
    echo -e "  ${RED}✗${NC} Daemon logged a profile change on no-op restart"
    echo "    Journal: $journal"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} No profile change logged on no-op restart"

# --------------------------------------------------------------------------
# 3. Create path — delete profile, restart, assert it gets created
#    This requires no containers to be using the profile, so we skip
#    if the delete fails.
# --------------------------------------------------------------------------
echo ""
echo "3. Profile creation on startup"
if ! ssh_vm "incus profile delete '$PROFILE_NAME'" &>/dev/null; then
    echo -e "  ${YELLOW}⊘${NC} Skipped — profile is in use by containers"
else
    restart_daemon

    if ! ssh_vm "incus profile show '$PROFILE_NAME'" &>/dev/null; then
        echo -e "  ${RED}✗${NC} Profile was not created on startup"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} Profile exists after daemon start"

    hash_after_create=$(get_profile_config "$HASH_KEY")
    if [[ -z "$hash_after_create" ]]; then
        echo -e "  ${RED}✗${NC} Profile hash key is empty"
        exit 1
    fi
    echo -e "  ${GREEN}✓${NC} Profile hash is set ($hash_after_create)"
    assert_eq "Hash matches expected" "$correct_hash" "$hash_after_create"

    journal=$(daemon_journal)
    assert_contains "Journal reports profile created" "$journal" "Profile 'kapsule-base': created"
fi

# ============================================================================
# Done
# ============================================================================

echo ""
echo "Profile sync tests passed!"
