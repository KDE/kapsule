# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Context dataclasses passed through pipeline steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..container_options import ContainerOptions
from ..incus_client import IncusClient
from ..models_generated import InstanceSource
from ..operations import OperationReporter

if TYPE_CHECKING:
    from ..host_config_sync import HostConfigSync


@dataclass
class CreateContext:
    """Context passed through the container creation pipeline.

    Pre-creation steps build up ``instance_config``, ``devices``, and
    ``source``; the creation step calls the Incus API; post-creation
    steps configure the running container.

    Steps should guard their own preconditions (e.g. check
    ``opts.session_mode`` before doing session-mode work).

    Option parsing is deferred to the pipeline so that image-level
    defaults (from the image's ``kapsule.default_options`` property)
    can be read after the image is cached locally.  Early steps
    populate ``image_fingerprint`` and ``image_defaults``; the
    ``parse_create_options`` step merges them with ``raw_options``
    and sets ``opts``.
    """

    name: str
    image: str
    raw_options: dict[str, object]
    user_home: str
    incus: IncusClient
    progress: OperationReporter
    host_config_sync: HostConfigSync

    # Populated by pipeline steps before option parsing
    image_fingerprint: str | None = None
    image_defaults: dict[str, object] = field(default_factory=dict)

    # Set by parse_create_options step (after image defaults are known)
    opts: ContainerOptions | None = None

    # Built up by pipeline steps
    instance_config: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    devices: dict[str, dict[str, str]] = field(
        default_factory=lambda: dict[str, dict[str, str]]()
    )
    source: InstanceSource | None = None


@dataclass
class UserSetupContext:
    """Context passed through user setup steps.

    Each step receives this context and performs its work to
    configure a host user inside a container.
    """

    container_name: str
    uid: int
    gid: int
    username: str
    home_dir: str
    container_home: str
    instance_config: dict[str, str]
    incus: IncusClient
    progress: OperationReporter
