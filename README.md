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

## Installation

### For Development

```bash
# Install with pip (editable mode)
pip install -e .
```

### Using kde-builder

Add to your `~/.config/kde-builder.yaml`:

```yaml
project kapsule:
  repository: kde:fernando/kapsule
  branch: master
  cmake-options: -DBUILD_KDE_COMPONENTS=ON -DINSTALL_PYTHON_CLI=ON -DVENDOR_PYTHON_DEPS=ON
```

Then run:

```bash
kde-builder kapsule
```

#### CMake Options

| Option | Description |
|--------|-------------|
| `BUILD_KDE_COMPONENTS` | Build Qt/KDE libraries (libkapsule-qt) |
| `INSTALL_PYTHON_CLI` | Install the Python CLI tool |
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

- Python >= 3.11
- Incus
- systemd

## License

- Python code: GPL-3.0-or-later
- libkapsule-qt: LGPL-2.1-or-later
- Build system files: BSD-3-Clause

## Contributing

This project is part of KDE. See https://community.kde.org/Get_Involved for how to contribute.
