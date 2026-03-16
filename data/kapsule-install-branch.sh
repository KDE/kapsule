#!/bin/bash
# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Update the kapsule sysext from a CI build.
#
# Usage: sudo kapsule-install-branch [branch]
#
# Examples:
#   sudo kapsule-install-branch              # latest build from master
#   sudo kapsule-install-branch master       # explicit master
#   sudo kapsule-install-branch work/my-fix  # test a merge request branch
#
# This queries KDE's GitLab API for the latest successful build-sysext
# job on the given ref, downloads the sysext image, and restarts kapsule.

set -euo pipefail

S3_HOST="storage.kde.org"
S3_BASE="ci-artifacts/kde-linux/kapsule/sysext/j"

GITLAB_API="https://invent.kde.org/api/v4"
PROJECT_ID=24978

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This script must be run as root" >&2
    echo "Usage: sudo $0 [branch]" >&2
    exit 1
fi

for cmd in curl jq importctl systemd-sysext systemctl; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: $cmd is required but not found" >&2
        exit 1
    fi
done

REF="${1:-master}"

echo "Looking up latest sysext build for ref '${REF}' ..."

# Walk recent successful pipelines on this ref and find a build-sysext job.
PIPELINES=$(curl -sf "${GITLAB_API}/projects/${PROJECT_ID}/pipelines?status=success&ref=${REF}&per_page=10")

if [ -z "$PIPELINES" ] || [ "$PIPELINES" = "[]" ]; then
    echo "Error: No successful pipelines found for ref '${REF}'" >&2
    echo "Check that the branch name is correct and that CI has run." >&2
    exit 1
fi

JOB_ID=""
for PIPELINE_ID in $(echo "$PIPELINES" | jq -r '.[].id'); do
    JOBS=$(curl -sf "${GITLAB_API}/projects/${PROJECT_ID}/pipelines/${PIPELINE_ID}/jobs")
    JOB_ID=$(echo "$JOBS" | jq -r '[.[] | select(.name == "build-sysext" and .status == "success")][0].id // empty')
    if [ -n "$JOB_ID" ]; then
        break
    fi
done

if [ -z "$JOB_ID" ]; then
    echo "Error: No successful build-sysext job found for ref '${REF}'" >&2
    echo "The branch may not have a sysext build yet, or it may have failed." >&2
    exit 1
fi

SYSEXT_URL="https://${S3_HOST}/${S3_BASE}/${JOB_ID}/kapsule.raw"

echo "Found build-sysext job ${JOB_ID}"
echo "Downloading ${SYSEXT_URL} ..."

importctl pull-raw --class=sysext --verify=no --force "${SYSEXT_URL}" kapsule

echo "Refreshing sysext ..."
systemd-sysext refresh

echo "Restarting kapsule daemon ..."
systemctl daemon-reload
systemctl restart kapsule-daemon.service

echo "Done. Kapsule updated from ref '${REF}' (job ${JOB_ID})."
systemd-sysext status
echo ""
echo "When you're done testing, revert to the OS version with:"
echo "  sudo /usr/lib/kapsule/kapsule-uninstall-branch"
