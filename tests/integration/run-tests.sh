#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Integration test runner for Kapsule
# Deploys sysext to test VM and runs all integration tests

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Test VM configuration
TEST_VM="${KAPSULE_TEST_VM:-192.168.100.129}"
VM_EXEC_MODE="${KAPSULE_VM_EXEC_MODE:-auto}"
SSH_OPTS="-o ConnectTimeout=5 -o StrictHostKeyChecking=no"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_SKIPPED=0

# ============================================================================
# Helper functions
# ============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $*"
    TESTS_PASSED=$(( TESTS_PASSED + 1 ))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $*"
    TESTS_FAILED=$(( TESTS_FAILED + 1 ))
}

log_skip() {
    echo -e "${YELLOW}[SKIP]${NC} $*"
    TESTS_SKIPPED=$(( TESTS_SKIPPED + 1 ))
}

ssh_vm() {
    local mode
    mode="$(resolve_vm_exec_mode)" || return 1

    if [[ "$mode" == "local" ]]; then
        if [[ $# -eq 1 ]]; then
            bash -lc "$1"
        else
            "$@"
        fi
    else
        ssh $SSH_OPTS "$TEST_VM" "$@"
    fi
}

scp_to_vm() {
    local mode
    mode="$(resolve_vm_exec_mode)" || return 1

    if [[ "$mode" == "local" ]]; then
        cp "$1" "$2"
    else
        scp $SSH_OPTS "$1" "$TEST_VM:$2"
    fi
}

is_local_vm_target() {
    [[ "$TEST_VM" == "localhost" || "$TEST_VM" == "127.0.0.1" || "$TEST_VM" == "::1" ]]
}

resolve_vm_exec_mode() {
    if [[ "$VM_EXEC_MODE" == "auto" ]]; then
        if is_local_vm_target; then
            echo "local"
        else
            echo "ssh"
        fi
        return
    fi

    if [[ "$VM_EXEC_MODE" == "local" || "$VM_EXEC_MODE" == "ssh" ]]; then
        echo "$VM_EXEC_MODE"
        return
    fi

    echo "ERROR: Invalid KAPSULE_VM_EXEC_MODE='$VM_EXEC_MODE' (expected auto|local|ssh)" >&2
    return 1
}

# Check VM is reachable
check_vm() {
    log_info "Checking test VM at $TEST_VM..."
    if ! ssh_vm "echo 'VM reachable'" &>/dev/null; then
        echo -e "${RED}ERROR: Cannot reach test VM at $TEST_VM${NC}"
        echo "Set KAPSULE_TEST_VM environment variable to override"
        exit 1
    fi
    log_info "VM is reachable"
}

# Deploy latest sysext to VM
deploy_sysext() {
    log_info "Deploying sysext to test VM..."
    if ! "$PROJECT_ROOT/sysext.sh"; then
        echo -e "${RED}ERROR: Failed to deploy sysext${NC}"
        exit 1
    fi
    log_info "Sysext deployed successfully"
    
    # Wait for daemon to be ready
    log_info "Waiting for kapsule-daemon to be ready..."
    local retries=10
    while ((retries > 0)); do
        if ssh_vm "busctl status org.kde.kapsule" &>/dev/null; then
            log_info "Daemon is ready"
            return 0
        fi
        sleep 1
        ((retries--))
    done
    echo -e "${RED}ERROR: Daemon did not become ready${NC}"
    exit 1
}

# Clean up test containers
cleanup_test_containers() {
    log_info "Cleaning up test containers..."
    ssh_vm 'for c in $(incus list -c n -f csv | grep "^test-"); do incus delete "$c" --force 2>/dev/null || true; done' || true
}

# ============================================================================
# Test runners
# ============================================================================

# Shell tests that exercise socket/mount functionality and should be
# run in both full-rootfs (default) and minimal-mount modes.
# test-host-mounts is excluded because it tests both modes internally.
SOCKET_TESTS=(
    "test-audio-sockets"
    "test-display-sockets"
    "test-dbus-socket"
)

is_socket_test() {
    local name="$1"
    for st in "${SOCKET_TESTS[@]}"; do
        if [[ "$name" == "$st" ]]; then
            return 0
        fi
    done
    return 1
}

# Run a single shell test file
# Usage: run_one_shell_test FILE [LABEL_SUFFIX]
run_one_shell_test() {
    local test_file="$1"
    local suffix="${2:-}"
    local test_name=$(basename "$test_file" .sh)
    local label="${test_name}${suffix}"

    echo ""
    log_info "Running $label..."

    if bash "$test_file"; then
        log_pass "$label"
    else
        log_fail "$label"
    fi
}

run_shell_tests() {
    log_info "Running shell-based integration tests..."

    # Pass 1: Run all tests with default settings (full rootfs)
    for test_file in "$SCRIPT_DIR"/test-*.sh; do
        if [[ -f "$test_file" ]]; then
            local test_name=$(basename "$test_file" .sh)

            # Apply test pattern filter if specified
            if [[ -n "$TEST_PATTERN" ]] && [[ "$test_name" != *"$TEST_PATTERN"* ]]; then
                continue
            fi

            run_one_shell_test "$test_file"
        fi
    done

    # Pass 2: Re-run socket tests with --no-host-rootfs
    log_info ""
    log_info "Re-running socket tests with --no-host-rootfs..."

    export KAPSULE_CREATE_FLAGS="--no-host-rootfs"
    for st in "${SOCKET_TESTS[@]}"; do
        local test_file="$SCRIPT_DIR/${st}.sh"
        if [[ -f "$test_file" ]]; then
            # Apply test pattern filter if specified
            if [[ -n "$TEST_PATTERN" ]] && [[ "$st" != *"$TEST_PATTERN"* ]]; then
                continue
            fi

            run_one_shell_test "$test_file" " [no-host-rootfs]"
        fi
    done
    unset KAPSULE_CREATE_FLAGS
}

run_python_tests() {
    log_info "Running Python-based integration tests..."

    # Check for local Python test files
    local test_files=("$SCRIPT_DIR"/test_*.py)
    if [[ ! -f "${test_files[0]}" ]]; then
        log_info "No Python tests found, skipping"
        return 0
    fi

    # Activate the project venv if present
    if [[ -f "$PROJECT_ROOT/.venv/bin/activate" ]]; then
        # shellcheck disable=SC1091
        source "$PROJECT_ROOT/.venv/bin/activate"
    fi

    # Check local pytest is available
    if ! python3 -m pytest --version &>/dev/null; then
        log_skip "Python tests (pytest not installed locally)"
        return 0
    fi

    # Set up an SSH tunnel for the VM's D-Bus system bus.
    # dbus-fast honours DBUS_SYSTEM_BUS_ADDRESS, so tests connect
    # through the forwarded socket transparently.
    local dbus_sock="/tmp/kapsule-test-dbus-$$.sock"
    rm -f "$dbus_sock"

    local dbus_address=""
    local ssh_tunnel_pid=""
    local mode
    mode="$(resolve_vm_exec_mode)" || {
        log_fail "Python tests (invalid VM exec mode)"
        return 0
    }

    if [[ "$mode" == "local" ]]; then
        log_info "Using local D-Bus system bus socket"
        dbus_address="unix:path=/run/dbus/system_bus_socket"
    else
        log_info "Opening SSH tunnel for D-Bus system bus..."
        ssh $SSH_OPTS -fNT \
            -L "$dbus_sock:/run/dbus/system_bus_socket" \
            "$TEST_VM"
        ssh_tunnel_pid=$!

        sleep 1

        if [[ ! -S "$dbus_sock" ]]; then
            echo -e "${RED}ERROR: D-Bus tunnel socket was not created${NC}"
            kill "$ssh_tunnel_pid" 2>/dev/null || true
            log_fail "Python tests (tunnel setup)"
            return 0
        fi

        dbus_address="unix:path=$dbus_sock"
    fi

    # Run pytest locally, pointing D-Bus at the tunnel and passing
    # the VM address so tests can run incus commands over SSH.
    echo ""
    log_info "Running pytest locally (D-Bus tunnelled to VM)..."
    if DBUS_SYSTEM_BUS_ADDRESS="$dbus_address" \
       KAPSULE_TEST_VM="$TEST_VM" \
       python3 -m pytest "$SCRIPT_DIR" -v --tb=short 2>&1; then
        log_pass "Python tests"
    else
        log_fail "Python tests"
    fi

    # Tear down the tunnel
    if [[ -n "$ssh_tunnel_pid" ]]; then
        kill "$ssh_tunnel_pid" 2>/dev/null || true
    fi
    rm -f "$dbus_sock"
}

# ============================================================================
# Main
# ============================================================================

print_usage() {
    cat <<EOF
Usage: $0 [OPTIONS] [TEST_PATTERN]

Run Kapsule integration tests against a test VM.

Options:
    -h, --help          Show this help
    -n, --no-deploy     Skip sysext deployment (use existing)
    -c, --cleanup-only  Only cleanup test containers, don't run tests
    -s, --shell-only    Only run shell tests
    -p, --python-only   Only run Python tests
    -k, --keep          Don't cleanup test containers after tests

Environment:
    KAPSULE_TEST_VM     Test VM address (default: 192.168.100.129)
    KAPSULE_VM_EXEC_MODE Execution mode: auto|local|ssh (default: auto)

Examples:
    $0                  Deploy and run all tests
    $0 -n               Run tests without redeploying
    $0 -s               Only run shell tests
    $0 test-create      Run only tests matching 'test-create'
EOF
}

# Parse arguments
DEPLOY=true
CLEANUP_ONLY=false
SHELL_ONLY=false
PYTHON_ONLY=false
KEEP_CONTAINERS=false
TEST_PATTERN=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            print_usage
            exit 0
            ;;
        -n|--no-deploy)
            DEPLOY=false
            shift
            ;;
        -c|--cleanup-only)
            CLEANUP_ONLY=true
            shift
            ;;
        -s|--shell-only)
            SHELL_ONLY=true
            shift
            ;;
        -p|--python-only)
            PYTHON_ONLY=true
            shift
            ;;
        -k|--keep)
            KEEP_CONTAINERS=true
            shift
            ;;
        *)
            TEST_PATTERN="$1"
            shift
            ;;
    esac
done

# Main execution
echo ""
echo "======================================"
echo "  Kapsule Integration Tests"
echo "======================================"
echo ""

check_vm

if [[ "$CLEANUP_ONLY" == "true" ]]; then
    cleanup_test_containers
    log_info "Cleanup complete"
    exit 0
fi

if [[ "$DEPLOY" == "true" ]]; then
    deploy_sysext
fi

cleanup_test_containers

if [[ "$PYTHON_ONLY" != "true" ]]; then
    run_shell_tests
fi

if [[ "$SHELL_ONLY" != "true" ]]; then
    run_python_tests
fi

if [[ "$KEEP_CONTAINERS" != "true" ]]; then
    cleanup_test_containers
fi

# Summary
echo ""
echo "======================================"
echo "  Test Summary"
echo "======================================"
echo -e "  ${GREEN}Passed:${NC}  $TESTS_PASSED"
echo -e "  ${RED}Failed:${NC}  $TESTS_FAILED"
echo -e "  ${YELLOW}Skipped:${NC} $TESTS_SKIPPED"
echo ""

if ((TESTS_FAILED > 0)); then
    exit 1
fi
exit 0
