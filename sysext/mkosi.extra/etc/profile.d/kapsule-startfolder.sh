#!/bin/sh
# SPDX-FileCopyrightText: 2026 Akseli Lahtinen <akselmo@akselmo.dev>
# SPDX-License-Identifier: GPL-3.0-or-later

if [ "$KAPSULE_START_DIR" != "" ]; then
   cd "$KAPSULE_START_DIR" || exit
fi
