# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Kapsule profile definitions and sync logic.

This module owns the kapsule-base profile: its content, its identity hash,
and the logic to create or update it via an IncusClient.  A SHA-256 hash of
the profile content is stored in the profile config so that on startup we can
detect whether the profile needs updating without fragile deep comparisons
or manual version bumps.
"""

from __future__ import annotations

import enum
import hashlib
import json

from .models_generated import ProfilePut, ProfilesPost

KAPSULE_PROFILE_NAME = "kapsule-base"

# Config key that holds the content hash of the profile definition
_HASH_KEY = "user.kapsule.profile-hash"


class ProfileSyncResult(enum.Enum):
    """Result of a profile ensure/sync operation."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


def _compute_profile_hash(
    config: dict[str, str],
    devices: dict[str, dict[str, str]],
    description: str,
) -> str:
    """Compute a content hash of the profile definition.

    The hash covers config (excluding the hash key itself), devices, and
    description so that any change to the profile source triggers an update.
    """
    canonical = json.dumps(
        {"config": config, "devices": devices, "description": description},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_profile() -> ProfilesPost:
    """Build the kapsule-base profile definition.

    A content hash is computed from the config, devices, and description
    and injected into the config under ``_HASH_KEY`` so that
    :func:`ensure_kapsule_profile` can detect stale profiles cheaply.

    Returns:
        ProfilesPost with the full profile definition including content hash.
    """
    description = "Kapsule base profile - privileged container with host integration"
    config: dict[str, str] = {
        # In a future version, we might investigate what
        # we can do with unprivileged containers.
        "security.privileged": "true",
        "security.nesting": "true",
        # Use host networking
        "raw.lxc": "lxc.net.0.type=none\n",
    }
    devices: dict[str, dict[str, str]] = {
        # Root disk - required for container storage
        "root": {
            "type": "disk",
            "path": "/",
            "pool": "default",
        },
        # GPU passthrough
        "gpu": {
            "type": "gpu",
        },
        # Mount the host filesystem at /.kapsule/host
        "hostfs": {
            "type": "disk",
            "source": "/",
            "path": "/.kapsule/host",
            "propagation": "rslave",
            "recursive": "true",
            "shift": "false",
        },
    }

    # Compute hash from the content, then inject it into config
    config[_HASH_KEY] = _compute_profile_hash(config, devices, description)

    return ProfilesPost(
        name=KAPSULE_PROFILE_NAME,
        description=description,
        config=config,
        devices=devices,
    )


async def ensure_kapsule_profile(client: "IncusClient") -> ProfileSyncResult:
    """Ensure the kapsule-base profile exists and matches the current source.

    Creates the profile if it doesn't exist, or updates it if the content
    hash stored in the profile config differs from the hash of the current
    profile definition.

    Args:
        client: An IncusClient instance for talking to Incus.

    Returns:
        ProfileSyncResult indicating what action was taken.
    """
    desired = build_profile()

    if not await client.profile_exists(KAPSULE_PROFILE_NAME):
        await client.create_profile(desired)
        return ProfileSyncResult.CREATED

    existing = await client.get_profile(KAPSULE_PROFILE_NAME)
    existing_hash = (existing.config or {}).get(_HASH_KEY, "")
    desired_hash = (desired.config or {}).get(_HASH_KEY, "")

    if existing_hash == desired_hash:
        return ProfileSyncResult.UNCHANGED

    # Content changed — push the update
    await client.update_profile(
        KAPSULE_PROFILE_NAME,
        ProfilePut(
            config=desired.config,
            devices=desired.devices,
            description=desired.description,
        ),
    )
    return ProfileSyncResult.UPDATED


# Avoid circular import — IncusClient is only needed at type-check time
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    from .incus_client import IncusClient
