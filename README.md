<!--
SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Kapsule

Incus-based container management with native KDE/Plasma integration.

A distrobox-like tool using Incus as the container/VM backend, designed for KDE Linux.

## Features

- **Nested containerization** - Create containers that can run Docker/Podman inside them
- **Host integration** - Containers share your home directory, user account, environment, Wayland/PipeWire sockets, and D-Bus session
- **KDE/Plasma integration** - Konsole integration, KIO worker, System Settings module (planned)
- **Terminal container markers** - `kapsule enter` emits OSC 777 `container;push/pop` markers so compatible terminals can detect container sessions

## Quick Start

```bash
# Create and enter your distro's default container
kapsule enter

# Create and enter a container
kapsule create my-dev --image images:ubuntu/24.04
kapsule enter my-dev

# Inside the container, you have access to:
# - Your home directory (mounted at /home/<username>)
# - Your user account (same UID/GID)
# - Your environment variables
# - Docker/Podman capability
```

## Development

Kapsule includes a purpose-built `kapsule:kapsule-dev` image containing the
Arch Linux build toolchain, Qt, KDE Frameworks, QCoro, Python development
tools, and a configured `kde-builder`. This is the supported development
environment and avoids requiring the same dependency versions on the host.

A working Kapsule installation is required to bootstrap the development
container. Create it from a terminal whose home contains a KDE source tree at
`~/kde`:

```bash
kapsule create kapsule-dev --image kapsule:kapsule-dev
kapsule enter kapsule-dev
```

The image does not mount the complete host home directory. It mounts `~/kde`
at the same path so source, build output, and logs persist when the development
container is recreated. Clone the repository at `~/kde/src/kapsule` if it is
not already there.

Build and install Kapsule inside the development container:

```bash
cd ~/kde/src/kapsule
kde-builder --no-src kapsule
sudo systemctl daemon-reload
sudo systemctl restart kapsule-daemon.service
```

The development image installs directly into `/usr`. Overwriting its packaged
Kapsule is intentional: the container is disposable, while the mounted
`~/kde` tree remains on the host. Recreate the container to return to a clean
environment.

Do not use `pip install -e .` as a project installation method. Kapsule's
user-facing CLI and Qt library are C++, and the Python daemon must be installed
with its systemd and D-Bus integration through CMake.

### CMake Options

| Option | Description |
|--------|-------------|
| `BUILD_KDE_COMPONENTS` | Build Qt/KDE libraries (libkapsule-qt) |
| `INSTALL_PYTHON_DAEMON` | Install the Python daemon and system integration |
| `VENDOR_PYTHON_DEPS` | Bundle Python dependencies with the installation |

## Commands

| Command | Description |
|---------|-------------|
| `kapsule create <name>` | Create a new container |
| `kapsule enter <name>` | Enter a container (interactive shell) |
| `kapsule enter <name> -- <cmd>` | Run a command in a container |
| `kapsule list` | List all containers |
| `kapsule list --running` | List running containers |
| `kapsule start <name>` | Start a stopped container |
| `kapsule stop <name>` | Stop a running container |
| `kapsule rm <name>` | Remove a container |

Use the short alias `kap` instead of `kapsule` for convenience:

```bash
kap create my-dev
kap enter my-dev
```

## Container Images

Kapsule uses Linux Containers images by default. Specify images with the `--image` flag:

```bash
# Ubuntu (default)
kapsule create dev --image images:ubuntu/24.04

# Fedora
kapsule create fedora-dev --image images:fedora/41

# Arch Linux
kapsule create arch-dev --image images:archlinux
```

See available images at: https://images.linuxcontainers.org

## How It Works

Kapsule creates Incus containers with a special profile that enables:

1. **Security nesting** - Allows running Docker/Podman inside the container
2. **Host networking** - Container shares the host's network namespace
3. **Device access** - GPU, audio, and display devices are available
4. **Home mount** - Your home directory is bind-mounted into the container

On first `enter`, Kapsule automatically:
- Creates your user account in the container (matching host UID/GID)
- Mounts your home directory
- Sets up XDG_RUNTIME_DIR symlink for Wayland/PipeWire

When `kapsule enter` runs in an interactive terminal (TTY), Kapsule emits OSC 777 markers:
- Enter: `container;push;<container-name>;kapsule`
- Exit: `container;pop;;`

This allows terminals such as Konsole and Ptyxis to track when the shell is inside a Kapsule container.

## Architecture

Kapsule consists of:

- **kapsule CLI** (C++) - User-facing command-line tool
- **libkapsule-qt** (C++) - Qt library for D-Bus communication
- **kapsule-daemon** (Python) - System service bridging D-Bus and Incus REST API

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed technical documentation.

## Requirements

- CMake >= 3.27
- Qt >= 6.6
- KDE Frameworks >= 6.0
- QCoro
- Python >= 3.11
- Incus
- systemd

## License

- Python code: GPL-3.0-or-later
- libkapsule-qt: LGPL-2.1-or-later
- Build system files: BSD-3-Clause

## Contributing

This project is part of KDE. See https://community.kde.org/Get_Involved for how to contribute.

### Integration Tests

The integration suite has three targets. Every target runs the same shell and
Python tests from `tests/integration/`; only the command transport changes.

The default target is the current `kapsule-dev` container. It tests the Kapsule
already installed in the container and does not build or deploy anything:

```bash
tests/integration/run-tests.sh
```

Use `--deploy` to build the current checkout, install it into the development
container's `/usr`, restart the daemon, and then run the tests:

```bash
tests/integration/run-tests.sh --deploy
```

An existing machine can be tested over SSH:

```bash
tests/integration/run-tests.sh --ssh user@example.org
```

The SSH account must have passwordless `sudo` for tests that modify host
configuration. Set `KAPSULE_TEST_SSH_ROOT_TARGET=root@example.org` instead when
the target permits direct root login. Additional options such as a non-default
port or identity file can be supplied with `KAPSULE_TEST_SSH_OPTIONS`.

The repository can also manage a disposable KDE Linux live VM. By default it
uses `kde-linux_202609272131.iso` in the repository root; override that with
`KAPSULE_KDE_LINUX_ISO` when needed:

```bash
tests/integration/run-tests.sh --target kde-linux-vm
```

Deployment is always separate and opt-in. Add `--deploy` to install the current
checkout into the local development container or to build and activate its
system extension on an SSH/VM target:

```bash
tests/integration/run-tests.sh --target kde-linux-vm --deploy
```

The managed VM can also be controlled directly:

```bash
tests/integration/kde-linux-vm.sh start
tests/integration/kde-linux-vm.sh ssh
tests/integration/kde-linux-vm.sh stop
```

The KDE Linux VM path relies on the following validated behavior:

- The live ISO boots under QEMU with UEFI firmware.
- An EROFS disk labeled `kde-openqa-ext` injects the SSH bootstrap.
- QEMU user networking forwards a host TCP port to guest port 22.
- The live session provisions working btrfs-backed Incus storage.
- QEMU VNC exposes the Plasma desktop for manual testing.

The default VNC endpoint is `vnc://127.0.0.1:5905`; the VM launcher prints the
actual SSH and VNC endpoints after startup.
