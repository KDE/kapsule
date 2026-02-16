# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Context dataclasses passed through pipeline steps."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..container_options import ContainerOptions
from ..incus_client import IncusClient
from ..models_generated import InstanceSource
from ..operations import OperationReporter


@dataclass
class CreateContext:
    """Context passed through the container creation pipeline.

    Pre-creation steps build up ``instance_config``, ``devices``, and
    ``source``; the creation step calls the Incus API; post-creation
    steps configure the running container.

    Steps should guard their own preconditions (e.g. check
    ``opts.session_mode`` before doing session-mode work).
    """

    name: str
    image: str
    opts: ContainerOptions
    incus: IncusClient
    progress: OperationReporter | None

    # Built up by pipeline steps
    instance_config: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    devices: dict[str, dict[str, str]] = field(default_factory=lambda: dict[str, dict[str, str]]())
    source: InstanceSource | None = None

    def info(self, msg: str) -> None:
        if self.progress:
            self.progress.info(msg)

    def dim(self, msg: str) -> None:
        if self.progress:
            self.progress.dim(msg)

    def warning(self, msg: str) -> None:
        if self.progress:
            self.progress.warning(msg)


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
    progress: OperationReporter | None

    def info(self, msg: str) -> None:
        if self.progress:
            self.progress.info(msg)

    def warning(self, msg: str) -> None:
        if self.progress:
            self.progress.warning(msg)
