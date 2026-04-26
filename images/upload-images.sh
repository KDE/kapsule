#!/bin/bash
# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Upload built kapsule images + simplestreams metadata to KDE S3 storage.
#
# Usage: ./scripts/upload-images.sh <s3-target-path>
#
# Examples:
#   ./scripts/upload-images.sh ci-artifacts/kde-linux/kapsule/images          # production
#   ./scripts/upload-images.sh ci-artifacts/kde-linux/kapsule/j/12345          # CI preview
#
# Requires:
#   - MINIO_OIDC environment variable (GitLab OIDC JWT)
#   - Built images in out/ and simplestreams metadata in streams/
#   - curl, jq

set -euo pipefail

S3_TARGET="${1:?Usage: $0 <s3-target-path>}"
S3_HOST="storage.kde.org"
TOKEN_URL="https://tokens.kde.org/minio/gitlab"

# --- Token redemption ---

if [ -z "${MINIO_OIDC:-}" ]; then
    echo "Error: MINIO_OIDC environment variable is not set" >&2
    exit 1
fi

echo "Redeeming OIDC token for S3 credentials ..."
CREDS=$(curl -sfL -X POST -d "token=${MINIO_OIDC}" "${TOKEN_URL}")

AWS_ACCESS_KEY_ID=$(echo "${CREDS}" | jq -r '.AccessKeyId')
AWS_SECRET_ACCESS_KEY=$(echo "${CREDS}" | jq -r '.SecretAccessKey')
AWS_SESSION_TOKEN=$(echo "${CREDS}" | jq -r '.SessionToken')

if [ -z "${AWS_ACCESS_KEY_ID}" ] || [ "${AWS_ACCESS_KEY_ID}" = "null" ]; then
    echo "Error: Failed to obtain S3 credentials from token endpoint" >&2
    exit 1
fi

# --- Install mc (MinIO CLI) if not available ---

if ! command -v mc &>/dev/null; then
    echo "Installing MinIO mc client ..."
    MC_DIR="${HOME}/.local/bin"
    mkdir -p "${MC_DIR}"
    curl -sfL -o "${MC_DIR}/mc" "https://dl.min.io/client/mc/release/linux-amd64/mc"
    chmod +x "${MC_DIR}/mc"
    export PATH="${MC_DIR}:${PATH}"
    if ! mc --version &>/dev/null; then
        echo "Error: Downloaded mc binary is not functional" >&2
        exit 1
    fi
fi

# --- Configure mc ---

# mc alias set doesn't support session tokens, so write the config directly.
MC_CONFIG_DIR="${HOME}/.mc"
mkdir -p "${MC_CONFIG_DIR}"
cat > "${MC_CONFIG_DIR}/config.json" <<EOF
{
  "version": "10",
  "aliases": {
    "kde": {
      "url": "https://${S3_HOST}",
      "accessKey": "${AWS_ACCESS_KEY_ID}",
      "secretKey": "${AWS_SECRET_ACCESS_KEY}",
      "sessionToken": "${AWS_SESSION_TOKEN}",
      "api": "S3v4",
      "path": "auto"
    }
  }
}
EOF
chmod 600 "${MC_CONFIG_DIR}/config.json"

# --- Prepare upload tree ---

echo "Preparing upload tree ..."
rm -rf upload-tree
mkdir -p upload-tree/streams/v1

# Copy simplestreams metadata
cp streams/v1/index.json streams/v1/images.json upload-tree/streams/v1/

# Copy image artifacts into paths matching what generate-simplestreams.py produced.
# The generator uses: images/<name>/<arch>/<version>/<file>
for image_out in out/*/; do
    image_name=$(basename "${image_out}")
    version=$(cat "${image_out}/version" 2>/dev/null || date +%Y%m%d)
    dest="upload-tree/images/${image_name}/amd64/${version}"
    mkdir -p "${dest}"
    cp "${image_out}/incus.tar.xz" "${dest}/"
    cp "${image_out}/rootfs.squashfs" "${dest}/"
done

# --- Upload ---

echo "Uploading to kde/${S3_TARGET}/ ..."
mc mirror --overwrite --retry upload-tree/ "kde/${S3_TARGET}/"

echo "Upload complete."
echo "Simplestreams index: https://${S3_HOST}/${S3_TARGET}/streams/v1/index.json"
