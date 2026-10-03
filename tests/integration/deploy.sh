#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd "$script_dir/../.." && pwd)
source "$script_dir/target.sh"

build_dir=${KAPSULE_TEST_SYSEXT_BUILD_DIR:-$project_root/sysext/mkosi.output}
image=$build_dir/kapsule.raw

case $KAPSULE_TEST_TARGET in
    local)
        local_build_dir=${KAPSULE_BUILD_DIR:-$project_root/../../build/kapsule}
        echo "Installing the current checkout into the local development container..."
        kde-builder --no-src --no-install kapsule
        sudo cmake --install "$local_build_dir"
        sudo systemctl daemon-reload
        sudo systemctl restart kapsule-daemon.service
        ;;
    ssh|kde-linux-vm)
        echo "Building Kapsule system extension..."
        sudo mkosi --directory="$project_root/sysext" build
        [[ -f $image ]] || {
            echo "Built system extension not found: $image" >&2
            exit 1
        }

        remote_image=/tmp/kapsule-test.raw
        target_copy_to "$image" "$remote_image"
        target_exec_root "install -d /var/lib/extensions && \
            install -m 0644 '$remote_image' /var/lib/extensions/kapsule.raw && \
            rm -f '$remote_image' && \
            systemd-sysext refresh && \
            systemctl daemon-reload && \
            systemctl restart kapsule-daemon.service"
        ;;
esac

target_exec "busctl status org.kde.kapsule >/dev/null"
echo "Kapsule deployment is active on $(target_description)"
