#!/bin/bash
# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Upload built kapsule images + simplestreams metadata to KDE S3 storage.
#
# Usage: ./images/upload-images.sh <s3-host-and-path>
#
# Examples:
#   ./images/upload-images.sh storage.kde.org/kapsule-images/                            # production
#   ./images/upload-images.sh storage.kde.org/ci-artifacts/kde-linux/kapsule/j/12345/    # CI preview
#
# Requires:
#   - MINIO_OIDC environment variable (GitLab OIDC JWT, audience
#     https://tokens.kde.org)
#   - Built images in out/ and simplestreams metadata in streams/
#   - python3, git, pip
#
# This delegates the actual upload to sysadmin/ci-utilities/sync-s3-folder.py
# rather than calling the S3 API directly. The KDE Tokens service that
# redeems MINIO_OIDC issues credentials whose policy is keyed on the
# S3 client + request shape produced by that script (kde-linux-packages
# uses the same recipe), so going through it is the path that's
# guaranteed to be authorised against buckets like kapsule-images/.

set -euo pipefail

S3_REMOTE="${1:?Usage: $0 <s3-host-and-path>}"

if [ -z "${MINIO_OIDC:-}" ]; then
    echo "Error: MINIO_OIDC environment variable is not set" >&2
    exit 1
fi

# --- Fetch ci-utilities and its dependencies ---
#
# Cloned fresh each run rather than baked into the CI image so the
# upload always uses whatever the sysadmin team has on master. Cheap
# enough -- the repo is small and the clone is shallow.
#
# Path convention follows ci-utilities's own README: "Clone the
# ci-utilities repository in your checkout of your project." In CI,
# CI_PROJECT_DIR is the runner-managed project checkout (cleaned
# between jobs); locally it's unset and we fall back to $PWD, which
# the script's working-directory expectations (out/, streams/) already
# require to be the project root. The .ci-utilities prefix marks the
# directory as build scaffolding rather than a real project subtree.

CI_UTILITIES_DIR="${CI_PROJECT_DIR:-$PWD}/.ci-utilities"
if [ ! -d "${CI_UTILITIES_DIR}" ]; then
    echo "Cloning sysadmin/ci-utilities into ${CI_UTILITIES_DIR} ..."
    git clone --depth=1 https://invent.kde.org/sysadmin/ci-utilities.git "${CI_UTILITIES_DIR}"
fi

# sync-s3-folder.py uses the minio python client; install it if missing.
# python-pip itself isn't shipped on every CI image (kde-linux-builder
# doesn't have it preinstalled), so install that first via pacman if
# needed. --break-system-packages is required on PEP 668 distros (Arch,
# recent Debian) where pip refuses to touch the system site-packages
# otherwise.
if ! python3 -c 'import minio' 2>/dev/null; then
    if ! command -v pip >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then
        echo "Installing python-pip ..."
        sudo pacman -Sy --noconfirm python-pip
    fi
    echo "Installing minio python client ..."
    python3 -m pip install minio --break-system-packages
fi

# --- Prepare upload tree ---
#
# generate-simplestreams.py writes its index under streams/v1/ and
# expects image files at images/<name>/<arch>/<version>/<file>.
# Mirror that layout into upload-tree/ so the sync uploads exactly the
# paths the simplestreams index references.

echo "Preparing upload tree ..."
rm -rf upload-tree
mkdir -p upload-tree/streams/v1

cp streams/v1/index.json streams/v1/images.json upload-tree/streams/v1/

for image_out in out/*/; do
    image_name=$(basename "${image_out}")
    version=$(cat "${image_out}/version" 2>/dev/null || date +%Y%m%d)
    dest="upload-tree/images/${image_name}/amd64/${version}"
    mkdir -p "${dest}"
    cp "${image_out}/incus.tar.xz" "${dest}/"
    cp "${image_out}/rootfs.squashfs" "${dest}/"
done

# --- Upload ---

echo "Uploading to ${S3_REMOTE} ..."
"${CI_UTILITIES_DIR}/sync-s3-folder.py" \
    --mode upload \
    --local upload-tree/ \
    --remote "${S3_REMOTE}" \
    --verbose

echo "Upload complete."
echo "Simplestreams index: https://${S3_REMOTE%/}/streams/v1/index.json"
