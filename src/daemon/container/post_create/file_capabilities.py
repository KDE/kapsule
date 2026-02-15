# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Post-create step: restore file capabilities stripped during image extraction."""

import subprocess

from ..contexts import PostCreateContext
from . import post_create_pipeline


@post_create_pipeline.step
async def fix_file_capabilities(ctx: PostCreateContext) -> None:
    """Restore file capabilities stripped during image extraction.

    Container images from linuxcontainers.org lose ``security.capability``
    extended attributes during image build or extraction.  Binaries like
    ``newuidmap`` / ``newgidmap`` (from the ``shadow`` package) need file
    capabilities (``cap_setuid+ep`` / ``cap_setgid+ep``) for rootless
    Podman / Docker to set up user namespaces inside the container.

    Upstream issue: https://github.com/lxc/lxc-ci/issues/955
    """
    caps: list[tuple[str, str]] = [
        ("/usr/bin/newuidmap", "cap_setuid+ep"),
        ("/usr/bin/newgidmap", "cap_setgid+ep"),
    ]
    for binary, cap in caps:
        result = subprocess.run(
            ["incus", "exec", ctx.name, "--", "setcap", cap, binary],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # Binary or setcap may not exist on every image — not fatal
            ctx.warning(f"Could not set {cap} on {binary}: {result.stderr.strip()}")
        else:
            ctx.dim(f"Set {cap} on {binary}")
