#!/bin/bash

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Shared execution boundary for the integration suite. Tests always invoke
# commands through target_exec, regardless of where Kapsule is running.

KAPSULE_TEST_TARGET=${KAPSULE_TEST_TARGET:-local}
KAPSULE_TEST_SSH_OPTIONS=${KAPSULE_TEST_SSH_OPTIONS:-}

target_ssh_options() {
    local -n result=$1
    result=(-o ConnectTimeout=5 -o StrictHostKeyChecking=no -o LogLevel=ERROR)
    if [[ -n $KAPSULE_TEST_SSH_OPTIONS ]]; then
        local extra_options
        read -r -a extra_options <<<"$KAPSULE_TEST_SSH_OPTIONS"
        result+=("${extra_options[@]}")
    fi
}

target_exec() {
    case $KAPSULE_TEST_TARGET in
        local)
            if (($# == 1)); then
                bash -lc "$1"
            else
                "$@"
            fi
            ;;
        ssh|kde-linux-vm)
            local options
            target_ssh_options options
            ssh "${options[@]}" "${KAPSULE_TEST_SSH_TARGET:?SSH target is not configured}" "$@"
            ;;
        *)
            echo "Unknown Kapsule test target: $KAPSULE_TEST_TARGET" >&2
            return 2
            ;;
    esac
}

target_exec_root() {
    case $KAPSULE_TEST_TARGET in
        local)
            if ((EUID == 0)); then
                target_exec "$@"
            elif (($# == 1)); then
                sudo bash -lc "$1"
            else
                sudo -- "$@"
            fi
            ;;
        ssh|kde-linux-vm)
            local options
            target_ssh_options options
            if [[ -n ${KAPSULE_TEST_SSH_ROOT_TARGET:-} ]]; then
                ssh "${options[@]}" "$KAPSULE_TEST_SSH_ROOT_TARGET" "$@"
            elif (($# == 1)); then
                ssh "${options[@]}" "$KAPSULE_TEST_SSH_TARGET" sudo bash -lc "$1"
            else
                ssh "${options[@]}" "$KAPSULE_TEST_SSH_TARGET" sudo -- "$@"
            fi
            ;;
    esac
}

target_copy_to() {
    local source=$1
    local destination=$2

    case $KAPSULE_TEST_TARGET in
        local)
            cp -- "$source" "$destination"
            ;;
        ssh|kde-linux-vm)
            local options
            target_ssh_options options
            scp "${options[@]}" "$source" \
                "${KAPSULE_TEST_SSH_TARGET:?SSH target is not configured}:$destination"
            ;;
    esac
}

target_description() {
    case $KAPSULE_TEST_TARGET in
        local) printf 'local kapsule-dev container\n' ;;
        ssh) printf 'SSH target %s\n' "$KAPSULE_TEST_SSH_TARGET" ;;
        kde-linux-vm) printf 'managed KDE Linux VM (%s)\n' "$KAPSULE_TEST_SSH_TARGET" ;;
    esac
}
