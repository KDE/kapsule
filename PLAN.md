# Kapsule - Incus-based Distrobox Alternative

A distrobox-like tool using Incus as the container/VM backend, with native KDE/Plasma integration. Ships with KDE Linux.

**CLI:** `kapsule` (alias: `kap`)

---

## Project Goals

1. **Primary:** Create containers that can run docker/podman inside them (nested containerization)
2. **Secondary:** Tight integration with KDE/Plasma (widget, KIO worker, System Settings module)
3. **Long-term:** Full distrobox feature parity with Incus backend

---

## Technology Stack

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    KDE Components (C++/QML)                     │
│   Plasma Widget  │  KIO Worker  │  KCM  │  Konsole Integration  │
└────────────────────────────┬────────────────────────────────────┘
                             │ D-Bus (org.kde.kapsule)
┌────────────────────────────▼────────────────────────────────────┐
│                    kapsule-daemon (Python)                      │
│   • D-Bus service for container lifecycle                       │
│   • Incus REST API client                                       │
│   • Feature → profile mapping                                   │
│   • Polkit integration for authorization                        │
└────────────────────────────┬────────────────────────────────────┘
                             │ Unix socket (REST)
┌────────────────────────────▼────────────────────────────────────┐
│                       Incus Daemon                              │
│                /var/lib/incus/unix.socket                       │
└─────────────────────────────────────────────────────────────────┘
```

### Why This Architecture

| Decision | Rationale |
|----------|-----------|
| **Python daemon** | Incus REST API is trivial with `httpx`. Fast iteration. No CGO/native binding complexity. |
| **D-Bus boundary** | Clean separation. KDE components only need to call D-Bus methods. Standard Linux IPC. |
| **C++ only where required** | KIO workers and KCM backends must be C++ (Qt plugin API). Keep them thin - just D-Bus calls. |
| **Python CLI** | Same codebase as daemon. `typer` for argument parsing. Instant development velocity. |

### Component Languages

| Component | Language | Build System | Framework |
|-----------|----------|--------------|-----------|
| `kapsule` CLI | Python 3.11+ | CMake | typer |
| `kapsule-daemon` | Python 3.11+ | CMake | dbus-fast, httpx |
| `libkapsule-qt` | C++ | CMake | Qt6, KF6 |
| Plasma Widget | QML | CMake | libplasma |
| KIO Worker | C++ | CMake | KIO |
| KCM Module | QML + C++ | CMake | KDeclarative |

### Python Dependencies

```
# Core
httpx           # Async HTTP client with Unix socket support
dbus-fast       # Fast async D-Bus library (Cython-accelerated)
typer           # CLI framework (type hints based)
rich            # Beautiful terminal output (typer dependency)
pyyaml          # Profile/config parsing
pydantic        # Data validation (also used for Incus API models)

# Development
pytest
pytest-asyncio
black
ruff
mypy
```

---

## Project Structure

```
kapsule/
├── CMakeLists.txt                  # Unified CMake build (Python + C++)
├── pyproject.toml                  # Python package definition
│
├── src/
│   ├── cli/                        # Python CLI package (kapsule)
│   │   ├── __init__.py
│   │   └── main.py                 # CLI entry point (typer app)
│   │
│   ├── daemon/                     # D-Bus service + Incus client
│   │   ├── __init__.py
│   │   ├── __main__.py             # Entry point: python -m src.daemon
│   │   ├── service.py              # org.kde.kapsule.Manager interface
│   │   ├── incus_client.py         # Async Incus REST client
│   │   └── models_generated.py     # Pydantic models from Incus OpenAPI
│   │
│   ├── libkapsule-qt/              # Qt wrapper for D-Bus API
│   │   ├── CMakeLists.txt
│   │   ├── kapsuleclient.h/.cpp    # KapsuleClient class
│   │   ├── container.h/.cpp        # Container data class
│   │   └── KapsuleConfig.cmake.in
│   │
│   ├── kio/                        # KIO worker (planned)
│   ├── kcm/                        # System Settings module (planned)
│   └── plasmoid/                   # Plasma widget (planned)
│
├── scripts/
│   ├── kapsule.in                  # Launcher script template
│   └── update_incus_models.py      # Regenerate models from OpenAPI
│
├── data/                           # (planned)
│   ├── profiles/                   # Default Incus profiles (YAML)
│   ├── dbus/                       # D-Bus service/config files
│   ├── polkit/                     # Polkit policy
│   └── systemd/                    # Systemd service files
│
└── tests/
    └── ...
```

---

## Building with kde-builder

### kde-builder Configuration

Add to `~/.config/kde-builder.yaml`:

```yaml
# Kapsule - from personal invent.kde.org repository
project kapsule:
  repository: kde:fernando/kapsule
  branch: main

# KDE dependencies for the Qt/KDE components (optional - only needed if 
# building KDE components and not using distro packages)
group kapsule-kde-deps:
  repository: kde-projects
  use-projects:
    - frameworks/extra-cmake-modules
    - frameworks/ki18n
    - frameworks/kcoreaddons
    - frameworks/kconfig
    - frameworks/kio
    - frameworks/kirigami
    - plasma/libplasma
```

> **Note:** The `kde:` prefix is a shortcut for `https://invent.kde.org/`. 
> Once kapsule moves to an official KDE location (e.g., `utilities/kapsule`), 
> it can be added to `sysadmin/repo-metadata` for automatic dependency resolution.

### Build Commands

```bash
# Build kapsule and all KDE dependencies
kde-builder kapsule

# Build only, no source update
kde-builder --no-src kapsule

# Run the CLI
kde-builder --run kapsule -- --help

# Run the daemon (for development)
kde-builder --run kapsule -- daemon
```

### CMake Build System

The project uses a unified CMake build system that handles both Python and C++ components:

```cmake
# CMakeLists.txt options
BUILD_KDE_COMPONENTS    # ON by default - builds libkapsule-qt
INSTALL_PYTHON_CLI      # ON by default - installs Python CLI
VENDOR_PYTHON_DEPS      # ON by default - vendors Python dependencies
```

**Key features:**
- ECM (Extra CMake Modules) for KDE integration
- Python dependency vendoring via pip at install time
- Configurable launcher script (`scripts/kapsule.in`)
- Separate vendor and kapsule Python paths

---

## D-Bus API

### Service Definition

**Bus:** Session bus (development) or System bus (production, for Polkit)  
**Name:** `org.kde.kapsule`  
**Path:** `/org/kde/kapsule`

### Interface: `org.kde.kapsule.Manager` (Implemented)

Current implementation in `src/daemon/service.py`:

```
interface org.kde.kapsule.Manager {
  methods:
    ListContainers() -> a(ssss)      # Returns [(name, status, image, created), ...]
    IsIncusAvailable() -> b          # Check if Incus daemon is accessible
    GetShellCommand(name: s) -> as   # Get command to enter container
  signals:
    ContainerStateChanged(name: s, state: s)
    OperationProgress(op_id: s, stage: s, progress: d, message: s)
  properties:
    readonly s Version
}
```

### Planned Methods (Not Yet Implemented)

```xml
<!-- Container lifecycle -->
<method name="CreateContainer">
  <arg name="name" type="s" direction="in"/>
  <arg name="image" type="s" direction="in"/>
  <arg name="features" type="as" direction="in"/>
  <arg name="operation_id" type="s" direction="out"/>
</method>

<method name="DeleteContainer">
  <arg name="name" type="s" direction="in"/>
  <arg name="force" type="b" direction="in"/>
</method>

<method name="StartContainer">
  <arg name="name" type="s" direction="in"/>
</method>

<method name="StopContainer">
  <arg name="name" type="s" direction="in"/>
  <arg name="force" type="b" direction="in"/>
</method>

<method name="GetContainer">
  <arg name="name" type="s" direction="in"/>
  <arg name="info" type="a{sv}" direction="out"/>
</method>

<!-- Features -->
<method name="ListFeatures">
  <arg name="features" type="as" direction="out"/>
</method>
```

### Polkit Actions

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1.0/policyconfig.dtd">
<policyconfig>
  <vendor>KDE</vendor>
  <vendor_url>https://kde.org</vendor_url>

  <!-- Enter container - no password for active desktop session -->
  <action id="org.kde.kapsule.enter-container">
    <description>Enter a Kapsule container</description>
    <message>Authentication is required to enter the container</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>yes</allow_active>
    </defaults>
  </action>

  <!-- Create/delete containers - password once, cached -->
  <action id="org.kde.kapsule.manage-container">
    <description>Create or delete Kapsule containers</description>
    <message>Authentication is required to manage containers</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin_keep</allow_active>
    </defaults>
  </action>

  <!-- System initialization - admin only -->
  <action id="org.kde.kapsule.initialize">
    <description>Initialize Kapsule system</description>
    <message>Authentication is required to initialize Kapsule</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
  </action>
</policyconfig>
```

---

## CLI Design

### Current Commands (Stub Implementations)

The CLI in `src/cli/main.py` uses typer. Commands currently print what they would do:

```bash
kapsule --version               # Show version
kapsule --help                  # Show help
kapsule create <name> [--image IMAGE] [--with-docker] [--with-graphics] ...
kapsule rm <name> [--force]
kapsule start <name>
kapsule stop <name>
kapsule enter <name> [--command CMD]
kapsule list [--all]
```

### Planned: CLI Talks to Daemon via D-Bus

Once complete, CLI will call D-Bus methods instead of Incus directly:

```bash
kapsule create <name> [--image IMAGE] [--with FEATURE]... [--without FEATURE]...
kapsule rm <name> [--force]
kapsule start <name>
kapsule stop <name> [--force]
kapsule enter <name>              # Interactive shell
kapsule exec <name> -- <command>  # Run command
kapsule list                      # List all containers
kapsule info <name>               # Container details
kapsule export <name> <app>       # Export .desktop file to host
kapsule features                  # List available features
kapsule init                      # First-time setup
kapsule daemon                    # Run D-Bus daemon (for development)
```

---

## Incus REST Client (Implemented)

The async Incus client is in `src/daemon/incus_client.py`:

```python
class IncusClient:
    """Async client for Incus REST API over Unix socket."""
    
    async def list_instances(recursion=1) -> list[Instance]
    async def list_containers() -> list[ContainerInfo]  # Simplified for D-Bus
    async def get_instance(name) -> Instance
    async def is_available() -> bool
```

Uses `httpx.AsyncHTTPTransport(uds=...)` for Unix socket communication.
Response models are Pydantic classes generated from Incus OpenAPI spec.

---

## Features (Incus Profiles)

User-facing **features** map to Incus **profiles** internally:

| Feature | Incus Profile | Description |
|---------|---------------|-------------|
| (base) | `kapsule-base` | Always applied - privileged container, host networking |
| `graphics` | `kapsule-graphics` | Wayland/X11 display access |
| `audio` | `kapsule-audio` | PipeWire/PulseAudio access |
| `dbus` | `kapsule-dbus` | Session D-Bus access |
| `home` | `kapsule-home` | Mount home directory |
| `gpu` | `kapsule-gpu` | GPU passthrough |

**Default:** All features enabled. Users disable with `--without`.

### Profile Definitions

Stored in `/usr/share/kapsule/profiles/` (system) and `~/.config/kapsule/profiles/` (user).

**kapsule-base.yaml** (always applied)
```yaml
config:
  security.privileged: "true"
  raw.lxc: |
    lxc.net.0.type=none
```

**kapsule-graphics.yaml** (Wayland + X11)
```yaml
config:
  environment.DISPLAY: "${DISPLAY}"
  environment.WAYLAND_DISPLAY: "${WAYLAND_DISPLAY}"
  environment.XDG_RUNTIME_DIR: "/run/user/1000"
devices:
  wayland:
    type: disk
    source: "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}"
    path: "/run/user/1000/${WAYLAND_DISPLAY}"
  x11:
    type: disk
    source: /tmp/.X11-unix
    path: /tmp/.X11-unix
```

**kapsule-audio.yaml** (PipeWire/PulseAudio)
```yaml
devices:
  pipewire:
    type: disk
    source: "${XDG_RUNTIME_DIR}/pipewire-0"
    path: "/run/user/1000/pipewire-0"
  pulse:
    type: disk
    source: "${XDG_RUNTIME_DIR}/pulse"
    path: "/run/user/1000/pulse"
```

**kapsule-home.yaml** (home directory)
```yaml
devices:
  home:
    type: disk
    source: "${HOME}"
    path: "${HOME}"
```

**kapsule-gpu.yaml** (GPU passthrough)
```yaml
devices:
  gpu:
    type: gpu
    gid: "video"
```

---

## KDE Linux Integration

### Build-Time Components (in KDE Linux image)

```
/usr/bin/kapsule                              # CLI (Python)
/usr/bin/kap                                  # Symlink to kapsule
/usr/lib/python3.x/site-packages/kapsule/    # Python package
/usr/lib/kapsule/kapsule-firstboot.sh        # First-boot script
/usr/share/kapsule/profiles/*.yaml           # Default profiles
/usr/share/kapsule/images/arch.tar.zst       # Pre-bundled image (~300MB)
/usr/share/dbus-1/system-services/org.kde.kapsule.service
/usr/share/polkit-1/actions/org.kde.kapsule.policy
/usr/lib/systemd/system/kapsule-daemon.service
/usr/lib/systemd/system/kapsule-init.service

# KDE components
/usr/lib/qt6/plugins/kf6/kio/kapsule.so      # KIO worker
/usr/share/plasma/plasmoids/org.kde.kapsule/ # Plasma widget
/usr/lib/qt6/plugins/plasma/kcms/kcm_kapsule.so
```

### First-Boot Service

```ini
# /usr/lib/systemd/system/kapsule-init.service
[Unit]
Description=Initialize Kapsule
ConditionPathExists=!/var/lib/kapsule/.initialized
After=incus.socket
Requires=incus.socket
Before=display-manager.service

[Service]
Type=oneshot
ExecStart=/usr/lib/kapsule/kapsule-firstboot.sh
ExecStartPost=/usr/bin/touch /var/lib/kapsule/.initialized
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

### Runtime Data

```
/var/lib/incus/                    # Incus storage, containers
/var/lib/kapsule/.initialized      # First-boot marker
~/.config/kapsule/config.yaml      # User preferences
~/.config/kapsule/profiles/        # Custom profiles
~/.local/share/kapsule/            # Exported .desktop files
```

---

## Development Phases

### Phase 1: Core Python Package ✅
- [x] Project structure with CMake build
- [x] Basic CLI scaffolding (typer-based)
- [x] Python package structure (`src/cli/`, `src/daemon/`)
- [x] Launcher script with vendored dependencies support
- [x] Async Incus REST client (`httpx` + Unix socket)
- [x] Pydantic models generated from Incus OpenAPI spec
- [ ] Container CRUD operations in daemon
- [ ] Test against real Incus instance

### Phase 2: D-Bus Daemon ✅ (Basic)
- [x] D-Bus daemon with `dbus-fast`
- [x] `ListContainers()`, `IsIncusAvailable()`, `GetShellCommand()` methods
- [x] `Version` property, progress signals defined
- [ ] Container lifecycle methods (Create, Delete, Start, Stop)
- [ ] Polkit integration for authorization
- [ ] CLI talks to daemon instead of Incus directly
- [ ] Systemd service file

### Phase 3: C++ Library (Partial)
- [x] `libkapsule-qt` library structure
- [x] `Container` data class with Qt property system
- [x] `KapsuleClient` D-Bus wrapper class (skeleton)
- [x] CMake build with ECM integration
- [ ] Actual D-Bus communication implementation
- [ ] Connect to running daemon

### Phase 4: Feature System
- [ ] Feature ↔ profile mapping
- [ ] Profile YAML loading and validation
- [ ] Variable expansion (`${HOME}`, `${XDG_RUNTIME_DIR}`, etc.)
- [ ] Profile registration with Incus

### Phase 5: KDE Components
- [ ] Plasma widget (container status, quick actions)
- [ ] KIO worker (`kapsule://container/path`)
- [ ] KCM System Settings module

### Phase 6: KDE Linux Integration
- [ ] First-boot service
- [ ] Pre-bundled container image
- [ ] Konsole integration (default to container)
- [ ] Seamless first-run experience

### Phase 7: Advanced Features
- [ ] Application export (distrobox-style `.desktop` files)
- [ ] VM support for stronger isolation
- [ ] Container updates/rebuilds
- [ ] Multi-user support

---

## Nested Container Requirements (v1 - Privileged)

For podman/docker inside privileged Incus containers:

1. **security.privileged: "true"** - container runs with full root privileges
2. Native overlayfs works directly (no fuse-overlayfs needed)
3. All capabilities available, no syscall filtering

**Security Note:** Privileged containers have no isolation from the host kernel. Acceptable for development/trusted workloads. Security hardening (unprivileged containers with user namespaces) planned for v2.

---

## References

- [Incus documentation](https://linuxcontainers.org/incus/docs/main/)
- [Incus REST API](https://linuxcontainers.org/incus/docs/main/rest-api/)
- [Incus images](https://images.linuxcontainers.org/)
- [Distrobox source](https://github.com/89luca89/distrobox)
- [kde-builder documentation](https://kde-builder.kde.org/)
- [KDE Frameworks 6](https://develop.kde.org/docs/frameworks/)
- [dbus-fast (Python D-Bus)](https://dbus-fast.readthedocs.io/)
- [httpx (Python HTTP client)](https://www.python-httpx.org/)
