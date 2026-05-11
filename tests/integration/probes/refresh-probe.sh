#!/bin/bash
# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Refresh-investigation probe.
#
# Drives a controlled refresh cycle end-to-end so we can investigate
# why `kapsule image refresh` has never worked in production. NOT a
# pass/fail test (yet) -- subcommands are designed for manual driving
# and emit maximally-diagnostic output. Once we understand the bug,
# this should evolve into a regression test.
#
# Architecture:
#   - Builds a minimal Arch rootfs once via mkosi (HOST side, ~30s).
#   - Packages it into an incus.tar.xz + rootfs.squashfs using the
#     real package-incus.sh + generate-simplestreams.py (HOST side,
#     ~5s per (re)package).
#   - Serves the resulting simplestreams tree over HTTPS on the test
#     VM at 127.0.0.1:18443 (VM side, via serve-streams.py).
#   - Generates a self-signed CA, installs it into the VM's system
#     trust store via `trust anchor --store` (additive -- daemon
#     continues to trust real CAs).
#   - Drives incus image pull / refresh via `curl --unix-socket
#     /var/lib/incus/unix.socket` against the daemon REST API.
#
# Requires on host: mkosi, scp, ssh, python3, openssl, mksquashfs, xz.
# Requires on VM:   python3, openssl (via serve-streams.py), incus,
#                   trust (p11-kit), root SSH access.
#
# State persists between invocations under PROBE_HOST_DIR (host) and
# PROBE_VM_DIR (VM). Run `teardown` to clean up.
#
# Usage:
#   refresh-probe.sh <subcommand> [args...]
#
# Environment overrides:
#   PROBE_FORCE_VERSION=<v>  Force the simplestreams version key for
#                            the next `build` (default: today's date
#                            + a monotonically increasing counter).
#                            Set to a fixed value across two builds to
#                            reproduce the version-dedup hypothesis.
#
# Subcommands:
#   build [v1|v2]          Build/repackage the fixture (v2 mutates).
#   sync                   scp the served tree to the VM.
#   start-server           Start HTTPS server on VM.
#   install-trust          Install CA cert into VM trust store.
#                          Restarts incus.
#   pull                   POST /1.0/images on VM (initial copy).
#   inspect                Show daemon's view of the cached image.
#   refresh                Call `kapsule image refresh` on VM.
#   diff                   Show what changed since the last checkpoint.
#   logs                   Tail incusd + server logs.
#   teardown               Reverse setup. Run this to clean up.
#   status                 Where are we, what files exist, what's running.
#
# Typical flow:
#   ./refresh-probe.sh build v1
#   ./refresh-probe.sh sync
#   ./refresh-probe.sh start-server
#   ./refresh-probe.sh install-trust
#   ./refresh-probe.sh pull
#   ./refresh-probe.sh inspect       # capture initial state
#   ./refresh-probe.sh build v2      # mutate
#   ./refresh-probe.sh sync
#   ./refresh-probe.sh refresh
#   ./refresh-probe.sh inspect       # what changed?
#   ./refresh-probe.sh diff
#   ./refresh-probe.sh teardown

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

readonly REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
readonly PROBE_HOST_DIR="${PROBE_HOST_DIR:-/tmp/kapsule-refresh-probe}"
readonly PROBE_VM_DIR="${PROBE_VM_DIR:-/tmp/kapsule-refresh-probe}"
readonly TEST_VM="${KAPSULE_TEST_VM:-192.168.100.129}"
readonly SERVER_PORT="${PROBE_SERVER_PORT:-18443}"
readonly SERVER_URL="https://127.0.0.1:${SERVER_PORT}/"
readonly IMAGE_ALIAS="kapsule-test-fixture"
# Trust-anchor file installed on the VM. Named so it is unmistakable
# in the system trust store and easy to spot/remove if teardown crashes.
readonly TRUST_ANCHOR_NAME="kapsule-refresh-probe-ca.pem"

# SSH wrappers -- always invoke as root because the probe needs to
# manage trust store, restart incus, write to /var/log, etc.
ssh_root() {
    ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o LogLevel=ERROR \
        "root@${TEST_VM}" "$@"
}

scp_to_vm() {
    scp -q -o StrictHostKeyChecking=no -o LogLevel=ERROR -r "$@" \
        "root@${TEST_VM}:${PROBE_VM_DIR}/"
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

readonly C_RED='\033[0;31m'
readonly C_GREEN='\033[0;32m'
readonly C_YELLOW='\033[1;33m'
readonly C_BLUE='\033[0;34m'
readonly C_DIM='\033[2m'
readonly C_RESET='\033[0m'

log_info()  { echo -e "${C_BLUE}[probe]${C_RESET} $*" >&2; }
log_step()  { echo -e "${C_GREEN}[probe]${C_RESET} $*" >&2; }
log_warn()  { echo -e "${C_YELLOW}[probe]${C_RESET} $*" >&2; }
log_error() { echo -e "${C_RED}[probe]${C_RESET} $*" >&2; }
log_dim()   { echo -e "${C_DIM}        $*${C_RESET}" >&2; }

# ---------------------------------------------------------------------------
# build -- (re)package the fixture image into the served tree
# ---------------------------------------------------------------------------
#
# This does NOT re-run mkosi (slow, ~30s). It only repackages the
# already-built rootfs into incus.tar.xz + rootfs.squashfs and
# regenerates the simplestreams metadata. Fast (~5s) and is what we
# call repeatedly for v1<->v2 cycling.
#
# To get a fresh rootfs, run mkosi explicitly:
#   mkosi --directory=tests/integration/fixtures/test-image build --force

cmd_build() {
    local marker="${1:-v1}"

    local mkosi_rootfs="${REPO_ROOT}/tests/integration/fixtures/test-image/mkosi.output/image"
    if [[ ! -d "${mkosi_rootfs}" ]]; then
        log_error "Fixture rootfs not found at ${mkosi_rootfs}"
        log_error "Run: mkosi --directory=tests/integration/fixtures/test-image build --force"
        return 1
    fi

    log_step "Building fixture (marker=${marker})"

    # We don't want to mutate the mkosi output in place (would invalidate
    # cache). Instead, copy the rootfs to a per-build dir and inject the
    # marker there.
    #
    # 229MB copy is non-trivial (~2s with cp --reflink on btrfs/xfs, ~5s
    # otherwise). If this becomes a hot path we can switch to overlayfs
    # or a hardlink-tree -- noting it for later.
    local rootfs_copy="${PROBE_HOST_DIR}/rootfs"
    mkdir -p "${PROBE_HOST_DIR}"
    rm -rf "${rootfs_copy}"
    cp -a --reflink=auto "${mkosi_rootfs}" "${rootfs_copy}"

    # Marker is the only thing that differs between v1 and v2. The
    # creation_date in metadata.yaml will also differ (set by
    # package-incus.sh from $(date +%s)), but the marker is the
    # deterministic, debuggable signal.
    echo "${marker} (built $(date -Iseconds))" > "${rootfs_copy}/etc/kapsule-test-marker"

    # Use the production package-incus.sh to produce incus.tar.xz +
    # rootfs.squashfs. This is the same code path CI runs, so the
    # output format is identical to production images -- which means
    # incus has no excuse to reject it.
    local out_dir="${PROBE_HOST_DIR}/out/test-fixture"
    rm -rf "${out_dir}"
    mkdir -p "${out_dir}"

    # Note: package-incus.sh requires write access to the rootfs dir
    # (it adjusts ownerships during squashfs build). Our copy above
    # is owned by us, so no sudo needed.
    "${REPO_ROOT}/images/package-incus.sh" \
        "${rootfs_copy}" \
        "${out_dir}" \
        "${REPO_ROOT}/tests/integration/fixtures/test-image/kapsule.yaml" \
        >&2

    # Override the version written by package-incus.sh to make it
    # deterministic and per-call-controllable. package-incus.sh now
    # derives version from $(date +%Y%m%d-%H%M%S), so back-to-back
    # builds normally land on different version keys -- but for the
    # probe we want explicit control so we can deliberately reproduce
    # same-version collisions (e.g. to test what happens when CI
    # publishes two builds at the exact same second, or to verify the
    # daemon-side cache invalidation behaviour).
    #
    # Strategy: maintain a monotonically-increasing counter at
    # ${PROBE_HOST_DIR}/version-counter and append it to today's date.
    # Result: 2026051101, 2026051102, ... -- strictly increasing so
    # simplestreams ordering is preserved, and unambiguous when reading
    # logs.
    #
    # Override behaviour with PROBE_FORCE_VERSION=<value> in the env
    # if you want to force a specific version (e.g. to deliberately
    # reproduce a same-version collision between two builds).
    local counter_file="${PROBE_HOST_DIR}/version-counter"
    local counter
    counter=$(( $(cat "${counter_file}" 2>/dev/null || echo 0) + 1 ))
    echo "${counter}" > "${counter_file}"
    local version="${PROBE_FORCE_VERSION:-$(date +%Y%m%d)$(printf '%02d' "${counter}")}"
    echo "${version}" > "${out_dir}/version"
    log_dim "  forced version:  ${version} (counter=${counter})"

    # Now run generate-simplestreams.py over a synthetic <images-src>
    # tree containing just our fixture's kapsule.yaml. The generator
    # walks <images-src>/<image-name>/kapsule.yaml and matches against
    # <out>/<image-name>/.
    local images_src="${PROBE_HOST_DIR}/images-src"
    rm -rf "${images_src}"
    mkdir -p "${images_src}/test-fixture"
    cp "${REPO_ROOT}/tests/integration/fixtures/test-image/kapsule.yaml" \
        "${images_src}/test-fixture/kapsule.yaml"

    local streams_dir="${PROBE_HOST_DIR}/streams"
    rm -rf "${streams_dir}"
    python3 "${REPO_ROOT}/images/generate-simplestreams.py" \
        "${images_src}" "${PROBE_HOST_DIR}/out" "${streams_dir}" >&2

    # Lay out the served tree. generate-simplestreams.py points at
    # `images/<name>/<arch>/<version>/<file>` paths (relative to the
    # server root). Mirror that layout under serve/.
    local serve_dir="${PROBE_HOST_DIR}/serve"
    rm -rf "${serve_dir}"
    mkdir -p "${serve_dir}/streams/v1"
    cp "${streams_dir}/v1/index.json" "${streams_dir}/v1/images.json" \
        "${serve_dir}/streams/v1/"

    local version
    version=$(cat "${out_dir}/version")
    local image_dest="${serve_dir}/images/test-fixture/amd64/${version}"
    mkdir -p "${image_dest}"
    cp "${out_dir}/incus.tar.xz" "${out_dir}/rootfs.squashfs" "${image_dest}/"

    log_info "Packaged size:"
    log_dim "  incus.tar.xz:    $(du -h "${out_dir}/incus.tar.xz" | cut -f1)"
    log_dim "  rootfs.squashfs: $(du -h "${out_dir}/rootfs.squashfs" | cut -f1)"
    log_dim "  marker:          ${marker}"
    log_dim "  version:         ${version}"

    # Stash the marker in a probe-state file so other subcommands can
    # report what was last built without re-reading the rootfs.
    echo "${marker}" > "${PROBE_HOST_DIR}/last-marker"
}

# ---------------------------------------------------------------------------
# sync -- copy the served tree to the test VM
# ---------------------------------------------------------------------------

cmd_sync() {
    local serve_dir="${PROBE_HOST_DIR}/serve"
    if [[ ! -d "${serve_dir}" ]]; then
        log_error "No serve dir to sync. Run: ./refresh-probe.sh build v1"
        return 1
    fi

    log_step "Syncing served tree to ${TEST_VM}:${PROBE_VM_DIR}"

    ssh_root "mkdir -p '${PROBE_VM_DIR}'" >/dev/null

    # Use rsync --delete to keep the VM's served dir in sync with the
    # host's (so v1->v2 swap actually removes v1 files). Falls back to
    # scp if rsync isn't on the host or VM.
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete -e "ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR" \
            "${serve_dir}/" "root@${TEST_VM}:${PROBE_VM_DIR}/serve/"
    else
        # Fallback: nuke and recopy. Less efficient but only depends
        # on scp.
        ssh_root "rm -rf '${PROBE_VM_DIR}/serve'"
        scp_to_vm "${serve_dir}"
    fi

    log_info "Sync complete. Files on VM:"
    ssh_root "find '${PROBE_VM_DIR}/serve' -type f | sort" | sed 's/^/        /' >&2
}

# ---------------------------------------------------------------------------
# start-server -- launch the HTTPS server on the VM
# ---------------------------------------------------------------------------

cmd_start_server() {
    local serve_dir="${PROBE_VM_DIR}/serve"
    local server_log="${PROBE_VM_DIR}/server.log"
    local pid_file="${PROBE_VM_DIR}/server.pid"

    # Stop any prior instance from a crashed previous run.
    if ssh_root "test -f '${pid_file}'"; then
        local old_pid
        old_pid=$(ssh_root "cat '${pid_file}'")
        log_warn "Killing stale server PID ${old_pid}"
        ssh_root "kill ${old_pid} 2>/dev/null || true"
        ssh_root "rm -f '${pid_file}'"
    fi

    # Copy serve-streams.py to the VM (needed once; cheap to redo).
    scp -q -o StrictHostKeyChecking=no -o LogLevel=ERROR \
        "${REPO_ROOT}/tests/integration/probes/serve-streams.py" \
        "root@${TEST_VM}:${PROBE_VM_DIR}/serve-streams.py"

    log_step "Starting HTTPS server on ${TEST_VM}:${SERVER_PORT}"

    # Run detached. nohup + &disown semantics over ssh: we redirect all
    # stdio so ssh closes its channel immediately and the python
    # process keeps running.
    #
    # The READY line goes to a file we then read so we know the server
    # is listening before we proceed. Using a file rather than capturing
    # ssh's stdout because once we add `&`, ssh detaches and we lose
    # stdout.
    ssh_root "rm -f '${PROBE_VM_DIR}/server.ready'"
    ssh_root "nohup python3 '${PROBE_VM_DIR}/serve-streams.py' \
                '${serve_dir}' --port ${SERVER_PORT} \
                > '${PROBE_VM_DIR}/server.ready' \
                2> '${server_log}' \
                & echo \$! > '${pid_file}' ; \
                disown 2>/dev/null || true"

    # Poll for READY (max 5s).
    local ready_line=""
    for i in 1 2 3 4 5 6 7 8 9 10; do
        ready_line=$(ssh_root "cat '${PROBE_VM_DIR}/server.ready' 2>/dev/null" || true)
        if [[ "${ready_line}" == READY\ * ]]; then
            break
        fi
        sleep 0.5
    done

    if [[ "${ready_line}" != READY\ * ]]; then
        log_error "Server did not become ready in 5s"
        log_error "Server log:"
        ssh_root "cat '${server_log}'" | sed 's/^/        /' >&2
        return 1
    fi

    # READY <port> <ca-pem-path> <pid>
    local pid ca_path
    read -r _ _ ca_path pid <<< "${ready_line}"
    log_info "Server up: pid=${pid} ca=${ca_path}"
    log_dim "Logs: ${server_log}"
    log_dim "Test: ssh root@${TEST_VM} curl -sS --cacert ${ca_path} ${SERVER_URL}streams/v1/index.json"
}

# ---------------------------------------------------------------------------
# install-trust -- add the server's CA to the VM's system trust store
# ---------------------------------------------------------------------------

cmd_install_trust() {
    local ca_src="${PROBE_VM_DIR}/serve/ca.pem"
    if ! ssh_root "test -f '${ca_src}'"; then
        log_error "CA cert not found on VM at ${ca_src}."
        log_error "Has the server been started? Run start-server first."
        return 1
    fi

    log_step "Installing CA into VM system trust store"

    # `trust anchor --store` is the additive trust install path on
    # p11-kit-based systems (which includes Arch / KDE Linux). Unlike
    # SSL_CERT_FILE, this adds our CA to the existing trust set rather
    # than replacing it -- so incus continues to trust real CAs (e.g.
    # for images.linuxcontainers.org).
    ssh_root "trust anchor --store '${ca_src}'"

    # Restart incus so its Go runtime re-reads the system CA bundle.
    # Live-loading isn't a thing for crypto/x509 -- it reads at process
    # start.
    log_info "Restarting incus to pick up new CA"
    ssh_root "systemctl restart incus && /usr/lib/incus/incusd waitready --timeout=60"
    log_info "incus active=$(ssh_root 'systemctl is-active incus')"
}

# ---------------------------------------------------------------------------
# pull -- initial image copy via daemon REST API
# ---------------------------------------------------------------------------
#
# Mirrors what IncusClient.download_remote_image does in the daemon, so
# the probe exercises the same incus code path our daemon does.

cmd_pull() {
    log_step "Triggering image pull via incus REST API"
    log_dim "  server:   ${SERVER_URL}"
    log_dim "  alias:    ${IMAGE_ALIAS}"

    local resp
    resp=$(ssh_root "curl -sS --unix-socket /var/lib/incus/unix.socket \
        http://localhost/1.0/images \
        -X POST -H 'Content-Type: application/json' \
        -d '$(cat <<JSON
{
  "auto_update": true,
  "aliases": [{"name": "${IMAGE_ALIAS}"}],
  "source": {
    "type": "image",
    "mode": "pull",
    "server": "${SERVER_URL}",
    "protocol": "simplestreams",
    "alias": "${IMAGE_ALIAS}"
  }
}
JSON
)'")

    log_dim "POST response:"
    echo "${resp}" | python3 -m json.tool 2>/dev/null | sed 's/^/        /' >&2 || echo "${resp}" | sed 's/^/        /' >&2

    local op
    op=$(echo "${resp}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("operation",""))')
    if [[ -z "${op}" ]]; then
        log_error "Did not receive an operation ID -- POST failed"
        return 1
    fi

    log_info "Waiting for operation ${op}"
    local result
    result=$(ssh_root "curl -sS --unix-socket /var/lib/incus/unix.socket \
        'http://localhost${op}/wait?timeout=60'")

    log_dim "Operation result:"
    echo "${result}" | python3 -m json.tool 2>/dev/null | sed 's/^/        /' >&2

    local status err
    status=$(echo "${result}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["metadata"].get("status",""))')
    err=$(echo "${result}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["metadata"].get("err",""))')

    if [[ "${status}" == "Success" ]]; then
        log_info "Pull succeeded"
    else
        log_error "Pull status=${status} err=${err}"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# inspect -- show daemon's view of the cached image
# ---------------------------------------------------------------------------

cmd_inspect() {
    local fp
    fp=$(ssh_root "incus image list local: '${IMAGE_ALIAS}' --format=csv -c f" 2>/dev/null | head -1)

    if [[ -z "${fp}" ]]; then
        log_warn "No local image found with alias '${IMAGE_ALIAS}'"
        return 0
    fi

    log_step "Image '${IMAGE_ALIAS}' fingerprint: ${fp}"

    # Full record via REST API (the CLI doesn't show update_source).
    local record
    record=$(ssh_root "curl -sS --unix-socket /var/lib/incus/unix.socket \
        'http://localhost/1.0/images/${fp}'")

    echo "${record}" | python3 -m json.tool | sed 's/^/        /' >&2

    # Capture the current state into a checkpoint file for later diff.
    local checkpoint="${PROBE_HOST_DIR}/last-inspect.json"
    mkdir -p "${PROBE_HOST_DIR}"
    echo "${record}" > "${checkpoint}"
    log_dim "Checkpoint saved to ${checkpoint}"

    # Try to read the in-image marker via metadata properties (the
    # marker file is in /etc/ inside the rootfs, not surfaced as an
    # incus property -- so this WILL fail unless we add it as a
    # metadata property in build).
    log_dim "Build-id marker (from probe state): $(cat "${PROBE_HOST_DIR}/last-marker" 2>/dev/null || echo 'unknown')"
}

# ---------------------------------------------------------------------------
# refresh -- call our daemon's refresh, capture access log delta
# ---------------------------------------------------------------------------

cmd_refresh() {
    local server_log="${PROBE_VM_DIR}/server.log"

    # Mark the current end of the server log so we can diff what
    # incus fetched during the refresh.
    local log_marker
    log_marker=$(ssh_root "wc -l < '${server_log}' 2>/dev/null || echo 0")

    log_step "Calling 'kapsule image refresh ${IMAGE_ALIAS}'"
    ssh_root "kapsule image refresh '${IMAGE_ALIAS}' 2>&1" | sed 's/^/        /' >&2

    log_info "HTTP requests during refresh:"
    ssh_root "tail -n +$((log_marker + 1)) '${server_log}' 2>/dev/null" | \
        sed 's/^/        /' >&2 || log_dim "(no server log entries)"
}

# ---------------------------------------------------------------------------
# diff -- show what changed between checkpoints
# ---------------------------------------------------------------------------

cmd_diff() {
    local prev="${PROBE_HOST_DIR}/last-inspect.json"
    if [[ ! -f "${prev}" ]]; then
        log_error "No checkpoint to diff against. Run 'inspect' first."
        return 1
    fi

    log_step "Capturing current state for diff"
    local fp
    fp=$(ssh_root "incus image list local: '${IMAGE_ALIAS}' --format=csv -c f" 2>/dev/null | head -1)
    if [[ -z "${fp}" ]]; then
        log_error "No image with alias '${IMAGE_ALIAS}' currently cached"
        return 1
    fi

    local current="${PROBE_HOST_DIR}/current-inspect.json"
    ssh_root "curl -sS --unix-socket /var/lib/incus/unix.socket \
        'http://localhost/1.0/images/${fp}'" > "${current}"

    log_info "Diff (previous vs current):"
    diff <(python3 -m json.tool "${prev}") <(python3 -m json.tool "${current}") | \
        sed 's/^/        /' >&2 || true

    # Move current to previous so subsequent diffs chain correctly.
    mv "${current}" "${prev}"
}

# ---------------------------------------------------------------------------
# logs -- show recent incusd + server log entries
# ---------------------------------------------------------------------------

cmd_logs() {
    log_step "Recent incusd entries (last 30):"
    ssh_root "tail -30 /var/log/incus/incusd.log" | sed 's/^/        /' >&2

    log_step "Server access log (last 30):"
    ssh_root "tail -30 '${PROBE_VM_DIR}/server.log' 2>/dev/null" | sed 's/^/        /' >&2 || \
        log_dim "(no server log)"
}

# ---------------------------------------------------------------------------
# status -- where are we?
# ---------------------------------------------------------------------------

cmd_status() {
    log_step "Probe state"

    log_info "Host work dir: ${PROBE_HOST_DIR}"
    if [[ -d "${PROBE_HOST_DIR}" ]]; then
        log_dim "  contents: $(ls "${PROBE_HOST_DIR}" 2>/dev/null | tr '\n' ' ')"
        log_dim "  last marker: $(cat "${PROBE_HOST_DIR}/last-marker" 2>/dev/null || echo '(none)')"
    else
        log_dim "  (does not exist)"
    fi

    log_info "VM (${TEST_VM}) work dir: ${PROBE_VM_DIR}"
    ssh_root "ls '${PROBE_VM_DIR}' 2>/dev/null" 2>/dev/null | sed 's/^/        /' >&2 || \
        log_dim "  (does not exist)"

    log_info "VM server status:"
    if ssh_root "test -f '${PROBE_VM_DIR}/server.pid'"; then
        local pid
        pid=$(ssh_root "cat '${PROBE_VM_DIR}/server.pid'")
        if ssh_root "kill -0 ${pid} 2>/dev/null"; then
            log_dim "  running (pid ${pid})"
        else
            log_dim "  pid file exists but process is dead"
        fi
    else
        log_dim "  not running"
    fi

    log_info "VM trust anchor:"
    if ssh_root "trust list --filter=ca-anchors 2>/dev/null | grep -q 'Kapsule Refresh Probe'"; then
        log_dim "  installed"
    else
        log_dim "  not installed"
    fi

    log_info "Cached image '${IMAGE_ALIAS}':"
    ssh_root "incus image list local: '${IMAGE_ALIAS}' --format=csv -c fadt 2>/dev/null" | \
        sed 's/^/        /' >&2 || log_dim "  (none)"
}

# ---------------------------------------------------------------------------
# teardown -- reverse setup; safe to run repeatedly
# ---------------------------------------------------------------------------

cmd_teardown() {
    log_step "Tearing down probe state"

    # Stop the server.
    if ssh_root "test -f '${PROBE_VM_DIR}/server.pid'"; then
        local pid
        pid=$(ssh_root "cat '${PROBE_VM_DIR}/server.pid' 2>/dev/null") || true
        if [[ -n "${pid}" ]]; then
            log_info "Stopping server pid ${pid}"
            ssh_root "kill ${pid} 2>/dev/null || true"
        fi
    fi

    # Remove the cached image (don't error if it doesn't exist).
    log_info "Deleting cached image '${IMAGE_ALIAS}' if present"
    ssh_root "incus image delete '${IMAGE_ALIAS}' 2>/dev/null || true"

    # Uninstall the CA anchor BEFORE removing the workdir (the file
    # the cert was loaded from would otherwise be gone, but `trust
    # anchor --remove` accepts both file paths and pkcs11: URIs --
    # we look up the anchor by its label so we don't depend on either).
    log_info "Removing CA from trust store (if installed)"
    ssh_root "
        URI=\$(trust list --filter=ca-anchors 2>&1 \
              | grep -B2 'Kapsule Refresh Probe Test CA' \
              | grep 'pkcs11:' | tr -d ' ' || true)
        if [ -n \"\${URI}\" ]; then
            trust anchor --remove \"\${URI}\" 2>/dev/null || true
            echo removed
        fi
    " | grep -q removed && {
        log_info "Restarting incus to drop CA"
        ssh_root "systemctl restart incus && /usr/lib/incus/incusd waitready --timeout=60"
    } || log_dim "  (no anchor to remove)"

    # Clean up VM workdir.
    log_info "Removing VM workdir ${PROBE_VM_DIR}"
    ssh_root "rm -rf '${PROBE_VM_DIR}'"

    # Clean up host workdir (preserve nothing -- next run rebuilds).
    log_info "Removing host workdir ${PROBE_HOST_DIR}"
    rm -rf "${PROBE_HOST_DIR}"

    log_info "Teardown complete."
    log_dim "(mkosi build output preserved at tests/integration/fixtures/test-image/mkosi.output/)"
}

# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

main() {
    local subcommand="${1:-}"
    if [[ -z "${subcommand}" ]]; then
        sed -n '5,55p' "$0" | sed 's/^# \?//' >&2
        exit 1
    fi
    shift

    case "${subcommand}" in
        build)         cmd_build "$@" ;;
        sync)          cmd_sync "$@" ;;
        start-server)  cmd_start_server "$@" ;;
        install-trust) cmd_install_trust "$@" ;;
        pull)          cmd_pull "$@" ;;
        inspect)       cmd_inspect "$@" ;;
        refresh)       cmd_refresh "$@" ;;
        diff)          cmd_diff "$@" ;;
        logs)          cmd_logs "$@" ;;
        status)        cmd_status "$@" ;;
        teardown)      cmd_teardown "$@" ;;
        *)
            log_error "Unknown subcommand: ${subcommand}"
            exit 1
            ;;
    esac
}

main "$@"
