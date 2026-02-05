# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Kapsule user configuration.

This module handles configuration with systemd-style layered precedence:

1. ~/.config/kapsule/kapsule.conf  (user overrides - highest priority)
2. /etc/kapsule/kapsule.conf       (admin/system overrides)
3. /usr/lib/kapsule/kapsule.conf   (package defaults - lowest priority)

Configuration options:
- default_container: Name of the default container to create/enter when none specified
- default_image: Default image to use when creating new containers
"""

import configparser
import os
from pathlib import Path
from typing import NamedTuple


class KapsuleConfig(NamedTuple):
    """User configuration for Kapsule."""

    default_container: str
    default_image: str


# Default values (used if no config files exist)
DEFAULT_CONTAINER_NAME = "kapsule"
DEFAULT_IMAGE = "images:ubuntu/24.04"


def get_config_paths(home_dir: str | None = None) -> list[Path]:
    """Get all config file paths in priority order (highest first).

    Args:
        home_dir: Home directory to use for user config. If None, uses current user's.

    Returns:
        List of paths to check, highest priority first.
    """
    paths: list[Path] = []

    # 1. User config (highest priority)
    if home_dir:
        # Use the provided home directory
        paths.append(Path(home_dir) / ".config" / "kapsule" / "kapsule.conf")
    else:
        config_home = os.environ.get("XDG_CONFIG_HOME", "")
        if not config_home:
            config_home = os.path.expanduser("~/.config")
        paths.append(Path(config_home) / "kapsule" / "kapsule.conf")

    # 2. System admin config
    paths.append(Path("/etc/kapsule/kapsule.conf"))

    # 3. Package defaults (lowest priority)
    paths.append(Path("/usr/lib/kapsule/kapsule.conf"))

    return paths


def get_config_path() -> Path:
    """Get the user config file path (for writing).

    Returns:
        Path to the user's config file.
    """
    config_home = os.environ.get("XDG_CONFIG_HOME", "")
    if not config_home:
        config_home = os.path.expanduser("~/.config")
    return Path(config_home) / "kapsule" / "kapsule.conf"


def load_config(home_dir: str | None = None) -> KapsuleConfig:
    """Load configuration from all config paths, merging with precedence.

    Reads config files from lowest to highest priority, with higher
    priority values overriding lower ones.

    Args:
        home_dir: Home directory to use for user config. If None, uses current user's.

    Returns:
        KapsuleConfig with merged settings.
    """
    # Start with hardcoded defaults
    default_container = DEFAULT_CONTAINER_NAME
    default_image = DEFAULT_IMAGE

    # Read in reverse priority order (lowest first, so higher overrides)
    for config_path in reversed(get_config_paths(home_dir=home_dir)):
        if not config_path.exists():
            continue

        parser = configparser.ConfigParser()
        try:
            parser.read(config_path)
        except configparser.Error:
            # Skip malformed config files
            continue

        if parser.has_section("kapsule"):
            if parser.has_option("kapsule", "default_container"):
                default_container = parser.get("kapsule", "default_container")
            if parser.has_option("kapsule", "default_image"):
                default_image = parser.get("kapsule", "default_image")

    return KapsuleConfig(
        default_container=default_container,
        default_image=default_image,
    )


def save_config(config: KapsuleConfig) -> None:
    """Save user configuration to disk.

    Always saves to the user config path (~/.config/kapsule/kapsule.conf).

    Args:
        config: Configuration to save.
    """
    config_path = get_config_path()

    # Ensure directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    parser["kapsule"] = {
        "default_container": config.default_container,
        "default_image": config.default_image,
    }

    with open(config_path, "w") as f:
        parser.write(f)
