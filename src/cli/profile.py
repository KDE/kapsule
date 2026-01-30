"""Kapsule profile definitions.

This module contains the profile configurations applied to Kapsule containers.
"""

from .models_generated import ProfilesPost

KAPSULE_PROFILE_NAME = "kapsule-base"

# Kapsule base profile - always applied to all Kapsule containers
# This profile sets up a privileged container with host integration
KAPSULE_BASE_PROFILE = ProfilesPost(
    name=KAPSULE_PROFILE_NAME,
    description="Kapsule base profile - privileged container with host integration",
    config={
        # In a future version, we might investigate what
        # we can do with unprivileged containers.
        "security.privileged": "true",
        "security.nesting": "true",
        # Use host networking
        "raw.lxc": "lxc.net.0.type=none\n",
    },
    devices={
        # Root disk - required for container storage
        "root": {
            "type": "disk",
            "path": "/",
            "pool": "default",
        },
        # X11 socket passthrough, not user-specific
        "x11": {
            "type": "disk",
            "source": "/tmp/.X11-unix",
            "path": "/tmp/.X11-unix",
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
    },
)
