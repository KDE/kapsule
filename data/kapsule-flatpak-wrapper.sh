#!/bin/bash
# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Kapsule Flatpak HostCommand wrapper
#
# Invoked by a patched flatpak-session-helper when FLATPAK_HOST_COMMAND_WRAPPER
# is set.  The session-helper prepends this script as argv[0] before the
# original HostCommand argv, so "$@" contains the command that the flatpak
# app wants to run on the host.
#
# Environment:
#   FLATPAK_HOST_COMMAND_SENDER  D-Bus unique name of the requesting app
#                                (injected by the patched session-helper).
#                                Reserved for future per-app container routing.
#
# Current behaviour: routes all HostCommands into the user's default kapsule
# container via `kapsule enter -- <command>`.  A future version will use the
# sender identity to select the correct container per application.

set -euo pipefail

if [ $# -eq 0 ]; then
    exit 1
fi

exec kapsule enter -- "$@"
