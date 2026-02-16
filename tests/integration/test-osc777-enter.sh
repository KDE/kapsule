#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Test: OSC 777 container push/pop emission on kapsule enter
#
# Verifies that `kapsule enter` emits:
#   - OSC 777 container;push;NAME;kapsule
#   - OSC 777 container;pop;;
# when run in a TTY context.

source "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

CONTAINER_NAME="test-osc777"

cleanup_container "$CONTAINER_NAME"

echo "Testing OSC 777 emission for kapsule enter..."

echo ""
echo "1. Create test container"
create_container "$CONTAINER_NAME" "images:alpine/edge" >/dev/null
assert_container_exists "$CONTAINER_NAME"
assert_container_state "$CONTAINER_NAME" "RUNNING"

echo ""
echo "2. Run kapsule enter through PTY and capture bytes"
output=$(ssh_vm "python3 - <<'PY'
import errno
import os
import pty
import select
import subprocess

name = 'test-osc777'
cmd = ['kapsule', 'enter', name, '--', 'true']
master, slave = pty.openpty()
proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave, close_fds=True)
os.close(slave)

buf = bytearray()
while True:
    if proc.poll() is not None:
        break

    readable, _, _ = select.select([master], [], [], 0.1)
    if master in readable:
        try:
            chunk = os.read(master, 4096)
        except OSError as e:
            if e.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        buf.extend(chunk)

while True:
    try:
        chunk = os.read(master, 4096)
    except OSError as e:
        if e.errno == errno.EIO:
            break
        raise
    if not chunk:
        break
    buf.extend(chunk)

os.close(master)
ret = proc.wait()
print(f'EXIT={ret}')
print(f'HEX={buf.hex()}')
PY" 2>&1) || {
    echo "PTY capture command failed"
    echo "$output"
    cleanup_container "$CONTAINER_NAME"
    exit 1
}

clean_output=$(echo "$output" | tr -d '\r')
exit_code=$(echo "$clean_output" | sed -n 's/^EXIT=//p' | tail -n1)
hex_output=$(echo "$clean_output" | sed -n 's/^HEX=//p' | tail -n1)

if [[ -z "$exit_code" ]]; then
    echo -e "  ${RED}✗${NC} Failed to parse kapsule enter exit code"
    echo "$output"
    cleanup_container "$CONTAINER_NAME"
    exit 1
fi

container_hex=$(printf '%s' "$CONTAINER_NAME" | od -An -tx1 | tr -d ' \n')
push_hex="1b5d3737373b636f6e7461696e65723b707573683b${container_hex}3b6b617073756c6507"
pop_hex="1b5d3737373b636f6e7461696e65723b706f703b3b07"

if [[ "$hex_output" != *"$push_hex"* ]]; then
    echo -e "  ${RED}✗${NC} OSC 777 push sequence emitted"
    echo "    String does not contain: $push_hex"
    echo "    Captured HEX: $hex_output"
    cleanup_container "$CONTAINER_NAME"
    exit 1
else
    echo -e "  ${GREEN}✓${NC} OSC 777 push sequence emitted"
fi

if [[ "$hex_output" != *"$pop_hex"* ]]; then
    echo -e "  ${RED}✗${NC} OSC 777 pop sequence emitted"
    echo "    String does not contain: $pop_hex"
    echo "    Captured HEX: $hex_output"
    cleanup_container "$CONTAINER_NAME"
    exit 1
else
    echo -e "  ${GREEN}✓${NC} OSC 777 pop sequence emitted"
fi

echo ""
echo "3. Cleanup"
cleanup_container "$CONTAINER_NAME"

echo ""
echo "OSC 777 enter emission test passed!"
