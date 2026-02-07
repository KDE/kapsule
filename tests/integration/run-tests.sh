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
TEST_VM="${KAPSULE_TEST_VM:-192.168.100.157}"
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
    ssh $SSH_OPTS "$TEST_VM" "$@"
}

scp_to_vm() {
    scp $SSH_OPTS "$1" "$TEST_VM:$2"
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

run_shell_tests() {
    log_info "Running shell-based integration tests..."
    
    for test_file in "$SCRIPT_DIR"/test-*.sh; do
        if [[ -f "$test_file" ]]; then
            local test_name=$(basename "$test_file" .sh)
            echo ""
            log_info "Running $test_name..."
            
            if bash "$test_file"; then
                log_pass "$test_name"
            else
                log_fail "$test_name"
            fi
        fi
    done
}

run_python_tests() {
    log_info "Running Python-based integration tests..."
    
    # Copy Python tests to VM
    local remote_test_dir="/tmp/kapsule-tests"
    ssh_vm "rm -rf $remote_test_dir && mkdir -p $remote_test_dir"
    
    # Copy test files
    scp_to_vm "$SCRIPT_DIR/conftest.py" "$remote_test_dir/" 2>/dev/null || true
    scp_to_vm "$SCRIPT_DIR/test_"*.py "$remote_test_dir/" 2>/dev/null || true
    
    # Check if we have Python tests
    if ! ssh_vm "ls $remote_test_dir/test_*.py" &>/dev/null; then
        log_info "No Python tests found, skipping"
        return 0
    fi
    
    # Run pytest on VM
    echo ""
    log_info "Running pytest on VM..."
    if ssh_vm "cd $remote_test_dir && python3 -m pytest -v --tb=short" 2>&1; then
        log_pass "Python tests"
    else
        log_fail "Python tests"
    fi
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
    KAPSULE_TEST_VM     Test VM address (default: 192.168.100.157)

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
