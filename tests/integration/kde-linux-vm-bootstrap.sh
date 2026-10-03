#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail

authorized_key=/usr/lib/kapsule-test-vm/authorized_key

install_key() {
    local account=$1
    local home uid gid

    home=$(getent passwd "$account" | cut -d: -f6)
    uid=$(id -u "$account")
    gid=$(id -g "$account")
    install -d -m 0700 -o "$uid" -g "$gid" "$home/.ssh"
    install -m 0600 -o "$uid" -g "$gid" "$authorized_key" \
        "$home/.ssh/authorized_keys"
}

install_key root
if id live &>/dev/null; then
    install_key live
fi

passwd -d root
passwd -u root
install -d /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/99-kapsule-test-vm.conf <<'EOF'
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PubkeyAuthentication yes
AuthenticationMethods publickey
EOF

ssh-keygen -A
systemctl enable --now sshd.service
