#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd "$script_dir/../.." && pwd)
state_dir=${KAPSULE_KDE_LINUX_VM_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/kapsule/kde-linux-vm}
iso=${KAPSULE_KDE_LINUX_ISO:-$project_root/kde-linux_202609272131.iso}
ssh_port=${KAPSULE_KDE_LINUX_VM_SSH_PORT:-2222}
vnc_display=${KAPSULE_KDE_LINUX_VM_VNC_DISPLAY:-5}
pid_file=$state_dir/qemu.pid
ssh_key=$state_dir/id_ed25519
bootstrap_disk=$state_dir/ssh-bootstrap.erofs
known_hosts=$state_dir/known_hosts

usage() {
    cat <<EOF
Usage: $0 COMMAND

Manage the disposable KDE Linux live VM used by Kapsule integration tests.

Commands:
    prepare     Build the SSH bootstrap disk
    start       Start the VM and wait for SSH
    stop        Gracefully stop the VM
    status      Print status and connection details
    ssh         Open a root shell in the VM
    connection  Print shell variables for the integration runner

Environment:
    KAPSULE_KDE_LINUX_ISO              ISO path (default: $iso)
    KAPSULE_KDE_LINUX_VM_DIR           Persistent VM state directory
    KAPSULE_KDE_LINUX_VM_SSH_PORT      Forwarded SSH port (default: 2222)
    KAPSULE_KDE_LINUX_VM_VNC_DISPLAY   VNC display (default: 5)
EOF
}

require_command() {
    command -v "$1" &>/dev/null || {
        echo "Required command not found: $1" >&2
        exit 1
    }
}

is_running() {
    [[ -f $pid_file ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

prepare() {
    local root

    require_command mkfs.erofs
    require_command ssh-keygen
    mkdir -p "$state_dir"
    if [[ ! -f $ssh_key ]]; then
        ssh-keygen -q -t ed25519 -N '' -C kapsule-kde-linux-vm -f "$ssh_key"
    fi

    root=$(mktemp -d "$state_dir/bootstrap.XXXXXX")
    install -D -m 0755 "$script_dir/kde-linux-vm-bootstrap.sh" \
        "$root/usr/lib/openqa-bootstrap"
    install -D -m 0644 "$ssh_key.pub" \
        "$root/usr/lib/kapsule-test-vm/authorized_key"
    install -d "$root/usr/lib/extension-release.d"
    # The KDE Linux live image's openQA generator recognizes this extension
    # name and invokes /usr/lib/openqa-bootstrap after merging it.
    cat >"$root/usr/lib/extension-release.d/extension-release.openqa" <<'EOF'
ID=_any
VERSION_ID=_any
BUILD_ID=_any
EOF
    rm -f "$bootstrap_disk"
    mkfs.erofs --quiet -L kde-openqa-ext "$bootstrap_disk" "$root"
    rm -rf "$root"
}

ssh_options() {
    printf '%s\n' \
        -i "$ssh_key" \
        -p "$ssh_port" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=no \
        -o "UserKnownHostsFile=$known_hosts"
}

start() {
    local options

    require_command qemu-system-x86_64
    [[ -f $iso ]] || {
        echo "KDE Linux ISO not found: $iso" >&2
        exit 1
    }
    if is_running; then
        echo "KDE Linux VM is already running (PID $(cat "$pid_file"))"
        return
    fi

    prepare
    cp /usr/share/edk2/x64/OVMF_VARS.4m.fd "$state_dir/OVMF_VARS.4m.fd"
    rm -f "$pid_file" "$known_hosts" "$state_dir/monitor.sock" "$state_dir/serial.log"

    qemu-system-x86_64 \
        -name kapsule-kde-linux-live \
        -enable-kvm \
        -machine q35,accel=kvm \
        -cpu host \
        -smp 8 \
        -m 24G \
        -drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd \
        -drive if=pflash,format=raw,file="$state_dir/OVMF_VARS.4m.fd" \
        -drive file="$bootstrap_disk",format=raw,readonly=on,if=virtio \
        -cdrom "$iso" \
        -device virtio-vga \
        -display "vnc=127.0.0.1:$vnc_display" \
        -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$ssh_port-:22" \
        -serial "file:$state_dir/serial.log" \
        -monitor "unix:$state_dir/monitor.sock,server=on,wait=off" \
        -daemonize \
        -pidfile "$pid_file"

    mapfile -t options < <(ssh_options)
    echo "Waiting for KDE Linux SSH..."
    for _ in {1..120}; do
        if ssh "${options[@]}" live@127.0.0.1 true 2>/dev/null; then
            status
            return
        fi
        sleep 2
    done
    echo "VM did not become reachable over SSH; see $state_dir/serial.log" >&2
    exit 1
}

stop() {
    if ! is_running; then
        rm -f "$pid_file"
        echo "KDE Linux VM is not running"
        return
    fi
    printf 'system_powerdown\n' | socat - "UNIX-CONNECT:$state_dir/monitor.sock" >/dev/null
    for _ in {1..30}; do
        if ! is_running; then
            rm -f "$pid_file"
            return
        fi
        sleep 1
    done
    echo "VM did not stop within 30 seconds; asking QEMU to quit" >&2
    printf 'quit\n' | socat - "UNIX-CONNECT:$state_dir/monitor.sock" >/dev/null || true
    for _ in {1..10}; do
        if ! is_running; then
            rm -f "$pid_file"
            return
        fi
        sleep 1
    done
    echo "QEMU did not terminate" >&2
    exit 1
}

status() {
    if ! is_running; then
        echo "KDE Linux VM is not running"
        return 1
    fi
    echo "KDE Linux VM is running (PID $(cat "$pid_file"))"
    echo "SSH: ssh -i $ssh_key -p $ssh_port live@127.0.0.1"
    echo "VNC:  vnc://127.0.0.1:$((5900 + vnc_display))"
}

connect_ssh() {
    local options
    mapfile -t options < <(ssh_options)
    exec ssh "${options[@]}" live@127.0.0.1
}

connection() {
    printf 'KAPSULE_TEST_SSH_TARGET=%q\n' live@127.0.0.1
    printf 'KAPSULE_TEST_SSH_ROOT_TARGET=%q\n' root@127.0.0.1
    printf 'KAPSULE_TEST_SSH_OPTIONS=%q\n' \
        "-i $ssh_key -o Port=$ssh_port -o IdentitiesOnly=yes -o UserKnownHostsFile=$known_hosts"
}

case ${1:-} in
    prepare) prepare ;;
    start) start ;;
    stop) require_command socat; stop ;;
    status) status ;;
    ssh) connect_ssh ;;
    connection) connection ;;
    *) usage >&2; exit 2 ;;
esac
