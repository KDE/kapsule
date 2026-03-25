# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Creation pipeline step: restore file capabilities and permissions stripped during image extraction."""

import subprocess

from ..contexts import CreateContext
from . import create_pipeline


@create_pipeline.step(order=200)
async def fix_file_capabilities(ctx: CreateContext) -> None:
    """Restore file capabilities and permissions stripped during image extraction.

    Container images from linuxcontainers.org lose ``security.capability``
    extended attributes during image build or extraction.  Binaries like
    ``newuidmap`` / ``newgidmap`` (from the ``shadow`` package) need file
    capabilities (``cap_setuid+ep`` / ``cap_setgid+ep``) for rootless
    Podman / Docker to set up user namespaces inside the container.

    Upstream issue: https://github.com/lxc/lxc-ci/issues/955

    Additionally, ``bubblewrap`` (used by Flatpak) needs to be setuid root
    so that Flatpak skips ``--disable-userns``.  That flag writes to
    ``/proc/sys/user/max_user_namespaces`` which is on a read-only procfs
    mount inside LXC containers, causing Flatpak to fail.  When bwrap is
    setuid, Flatpak falls back to seccomp filtering instead.
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
            ctx.progress.warning(
                f"Could not set {cap} on {binary}: {result.stderr.strip()}"
            )
        else:
            ctx.progress.dim(f"Set {cap} on {binary}")

    # Make bubblewrap setuid so Flatpak works inside containers.
    # LXC mounts /proc/sys read-only, which breaks bwrap's --disable-userns
    # (it needs to write to /proc/sys/user/max_user_namespaces).  When bwrap
    # is setuid, Flatpak uses seccomp filtering instead.
    result = subprocess.run(
        ["incus", "exec", ctx.name, "--", "chmod", "u+s", "/usr/bin/bwrap"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        ctx.progress.warning(
            f"Could not set setuid on /usr/bin/bwrap: {result.stderr.strip()}"
        )
    else:
        ctx.progress.dim("Set setuid on /usr/bin/bwrap (Flatpak support)")
