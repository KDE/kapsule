# Kapsule

Incus-based container management with native KDE/Plasma integration.

A distrobox-like tool using Incus as the container/VM backend, designed for KDE Linux.

## Features

- Create containers that can run Docker/Podman inside them (nested containerization)
- Tight KDE/Plasma integration (widget, KIO worker, System Settings module)
- Feature-based container configuration (graphics, audio, home mount, GPU)
- D-Bus API for system integration

## Building

### Prerequisites

**For Python CLI:**
- Python >= 3.11
- Meson >= 1.0.0
- Ninja

**For KDE Components (optional):**
- Qt >= 6.6
- KDE Frameworks >= 6.0
- Extra CMake Modules (ECM)

### Quick Start

```bash
# Configure and build (Python CLI only)
meson setup build
meson compile -C build

# Install
sudo meson install -C build

# Or for development (editable install)
pip install -e .
```

### Building with KDE Components

```bash
# Configure with KDE components
meson setup build -Dkde_components=enabled
meson compile -C build

# Build KDE components with CMake
cd kde
cmake -B build -DCMAKE_INSTALL_PREFIX=/usr
cmake --build build
sudo cmake --install build
```

### Using kde-builder

Add to your `~/.config/kde-builder.yaml`:

```yaml
project kapsule:
  repository: kde:fernando/kapsule
  branch: main
  override-build-system: meson
  meson-options: -Dkde_components=true
```

Then run:

```bash
kde-builder kapsule
```

## Usage

```bash
# Create a container
kapsule create my-dev --image ubuntu:24.04 --with-docker

# List containers
kapsule list

# Enter a container
kapsule enter my-dev

# Stop and remove
kapsule stop my-dev
kapsule rm my-dev
```

Or use the short alias:

```bash
kap create my-dev
kap enter my-dev
```

## Project Structure

```
kapsule/
├── meson.build              # Top-level build (Python components)
├── pyproject.toml           # Python package definition
├── src/kapsule/             # Python package
│   ├── cli/                 # CLI commands
│   ├── daemon/              # D-Bus service (TODO)
│   └── incus/               # Incus REST client (TODO)
├── kde/                     # KDE components (CMake)
│   ├── CMakeLists.txt
│   └── libkapsule-qt/       # Qt wrapper for D-Bus API
└── data/                    # Config files (TODO)
    ├── dbus/
    ├── polkit/
    └── systemd/
```

## License

- Python code: GPL-3.0-or-later
- libkapsule-qt: LGPL-2.1-or-later
- Build system files: BSD-3-Clause

## Contributing

This project is part of KDE. See https://community.kde.org/Get_Involved for how to contribute.
