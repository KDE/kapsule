<!--
SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>

SPDX-License-Identifier: GPL-3.0-or-later
-->

- Don't ammend commits unless explicitly asked to do so.

# Kapsule

## Build & Deploy
- Build using `kde-builder --no-src --build-when-unchanged kapsule` instead of trying to invoke cmake yourself.
- There is a file called `deploy-to-lasaths-test-vm.sh` that will build and deploy a sysext to a VM that you can connect to by running `ssh fernie@192.168.100.129`. If you need sudo, instead do `ssh root@192.168.100.129`.

## Python Code
- Never use `Any`. If you need to add new methods to incus_client, use the models from `models_generated`.
