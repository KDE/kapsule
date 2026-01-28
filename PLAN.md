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
| `kapsule-daemon` | Python 3.11+ | CMake | dbus-next, httpx |
| `libkapsule-qt` | C++ | CMake | Qt6, KF6 |
| Plasma Widget | QML | CMake | libplasma |
| KIO Worker | C++ | CMake | KIO |
| KCM Module | QML + C++ | CMake | KDeclarative |

### Python Dependencies

```
# Core
httpx           # Async HTTP client with Unix socket support
dbus-next       # Modern async D-Bus library  
typer           # CLI framework (type hints based)
rich            # Beautiful terminal output (typer dependency)
pyyaml          # Profile/config parsing
pydantic        # Data validation

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
│   │   ├── __init__.py             # Package init with version
│   │   └── main.py                 # CLI entry point (typer app)
│   │
│   ├── daemon/                     # D-Bus service
│   │   ├── __init__.py
│   │   ├── service.py              # org.kde.kapsule implementation
│   │   └── polkit.py               # Authorization helpers
│   │
│   ├── incus/                      # Incus REST client
│   │   ├── __init__.py
│   │   ├── client.py               # HTTP client wrapper
│   │   ├── containers.py           # Container operations
│   │   ├── images.py               # Image operations
│   │   └── profiles.py             # Profile operations
│   │
│   ├── features/                   # Feature → profile mapping
│   │   ├── __init__.py
│   │   ├── registry.py             # Feature definitions
│   │   └── resolver.py             # --with/--without logic
│   │
│   ├── config.py                   # Configuration handling
│   │
│   ├── libkapsule-qt/              # Qt wrapper for D-Bus API
│   │   ├── CMakeLists.txt
│   │   ├── kapsuleclient.h         # KapsuleClient class
│   │   ├── kapsuleclient.cpp
│   │   ├── container.h             # Container data class
│   │   ├── container.cpp
│   │   └── KapsuleConfig.cmake.in
│   │
│   ├── kio/                        # KIO worker
│   │   ├── CMakeLists.txt
│   │   └── kio_kapsule.cpp
│   │
│   ├── kcm/                        # System Settings module
│   │   ├── CMakeLists.txt
│   │   ├── kcm_kapsule.cpp
│   │   └── ui/
│   │       └── main.qml
│   │
│   └── plasmoid/                   # Plasma widget
│       ├── CMakeLists.txt
│       └── package/
│           ├── metadata.json
│           └── contents/
│               └── ui/
│                   └── main.qml
│
├── scripts/
│   ├── kapsule.in                  # Launcher script template
│   ├── kapsule-firstboot.sh        # First-boot initialization
│   └── container-setup.sh          # Runs inside new containers
│
├── data/
│   ├── profiles/                   # Default Incus profiles (YAML)
│   │   ├── kapsule-base.yaml
│   │   ├── kapsule-graphics.yaml
│   │   ├── kapsule-audio.yaml
│   │   ├── kapsule-dbus.yaml
│   │   ├── kapsule-home.yaml
│   │   └── kapsule-gpu.yaml
│   ├── dbus/
│   │   ├── org.kde.kapsule.service
│   │   └── org.kde.kapsule.conf
│   ├── polkit/
│   │   └── org.kde.kapsule.policy
│   ├── systemd/
│   │   ├── kapsule-daemon.service
│   │   └── kapsule-init.service
│   └── applications/
│       └── org.kde.kapsule.desktop
│
└── tests/
    ├── conftest.py
    ├── test_cli/
    ├── test_incus/
    └── test_daemon/
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

**Bus:** System bus (for Polkit integration)  
**Name:** `org.kde.kapsule`  
**Path:** `/org/kde/kapsule`

### Interface: `org.kde.kapsule.Manager`

```xml
<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
  "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node name="/org/kde/kapsule">
  <interface name="org.kde.kapsule.Manager">
    
    <!-- Container lifecycle -->
    <method name="CreateContainer">
      <arg name="name" type="s" direction="in"/>
      <arg name="image" type="s" direction="in"/>
      <arg name="features" type="as" direction="in"/>
      <arg name="container_path" type="o" direction="out"/>
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
    
    <!-- Queries -->
    <method name="ListContainers">
      <arg name="containers" type="a(ssss)" direction="out"/>
      <!-- Returns array of (name, status, image, created) -->
    </method>
    
    <method name="GetContainer">
      <arg name="name" type="s" direction="in"/>
      <arg name="info" type="a{sv}" direction="out"/>
    </method>
    
    <!-- Shell access -->
    <method name="GetShellCommand">
      <arg name="name" type="s" direction="in"/>
      <arg name="command" type="as" direction="out"/>
      <!-- Returns ["incus", "exec", "name", "--", "sudo", "-u", "user", "-i"] -->
    </method>
    
    <method name="EnsureDefaultContainer">
      <arg name="name" type="s" direction="out"/>
      <!-- Creates user's default container if missing, returns name -->
    </method>
    
    <!-- Features (maps to Incus profiles internally) -->
    <method name="ListFeatures">
      <arg name="features" type="as" direction="out"/>
      <!-- Returns: ["graphics", "audio", "home", "gpu", ...] -->
    </method>
    
    <!-- Signals -->
    <signal name="ContainerStateChanged">
      <arg name="name" type="s"/>
      <arg name="state" type="s"/>
    </signal>
    
    <!-- Properties -->
    <property name="Version" type="s" access="read"/>
    <property name="IncusAvailable" type="b" access="read"/>
    
  </interface>
</node>
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

### Current Implementation (Stub)

The CLI is implemented with typer in `src/cli/main.py`. Current commands are stubs that print what they would do:

```bash
kapsule --version               # Show version
kapsule --help                  # Show help

# Container lifecycle (stub implementations)
kapsule create <name> [--image IMAGE] [--with-docker] [--with-graphics] [--with-audio] [--with-home]
kapsule rm <name> [--force]
kapsule start <name>
kapsule stop <name>
kapsule enter <name> [--command CMD]
kapsule list [--all]
```

### Planned Commands (Full Feature Set)

```bash
# Container lifecycle
kapsule create <name> [--image IMAGE] [--with FEATURE]... [--without FEATURE]...
kapsule rm <name> [--force]
kapsule start <name>
kapsule stop <name> [--force]

# Enter containers
kapsule enter <name>              # Interactive shell
kapsule exec <name> -- <command>  # Run command

# List and inspect
kapsule list                      # List all containers
kapsule info <name>               # Container details

# Application export (distrobox-style)
kapsule export <name> <app>       # Export .desktop file to host

# Feature management
kapsule features                  # List available features
kapsule features show <name>      # Show feature details

# System
kapsule init                      # First-time setup
kapsule daemon                    # Run D-Bus daemon (for development)

# Examples
kap create arch-dev                              # All default features
kap create arch-dev --without audio              # Exclude audio
kap create arch-dev --without audio --without gpu  # Minimal graphics
kap create headless --without graphics --without audio --without gpu
kap enter arch-dev
```

### Current CLI Implementation

```python
# src/cli/main.py (excerpt - current stub implementation)
import typer
from rich.console import Console
from typing import Optional

app = typer.Typer(
    name="kapsule",
    help="Incus-based container management with KDE integration",
)
console = Console()

@app.command()
def create(
    name: str = typer.Argument(..., help="Name of the container to create"),
    image: str = typer.Option("ubuntu:24.04", "--image", "-i"),
    with_docker: bool = typer.Option(False, "--with-docker"),
    with_graphics: bool = typer.Option(True, "--with-graphics"),
    with_audio: bool = typer.Option(True, "--with-audio"),
    with_home: bool = typer.Option(True, "--with-home"),
) -> None:
    """Create a new kapsule container."""
    console.print(f"[bold green]Creating container:[/bold green] {name}")
    # TODO: Implement actual container creation via Incus REST API
    console.print("[yellow]⚠ Stub implementation - not yet functional[/yellow]")
```

### Planned CLI Implementation (with D-Bus client)

```python
# src/kapsule/cli/main.py
import asyncio
from typing import Annotated, Optional

import typer
from rich import print
from rich.table import Table

from kapsule.daemon.client import KapsuleClient
from kapsule.features import resolve_features

app = typer.Typer(help="Kapsule - Incus-based container manager with KDE integration.")
client = KapsuleClient()


@app.command()
def create(
    name: str,
    image: Annotated[str, typer.Option("--image", "-i", help="Base image")] = "archlinux",
    with_features: Annotated[list[str], typer.Option("--with", help="Features to enable")] = [],
    without: Annotated[list[str], typer.Option(help="Features to disable")] = [],
):
    """Create a new container.
    
    By default, all features are enabled (graphics, audio, home, gpu).
    Use --without to disable specific features.
    """
    features = resolve_features(with_features, without)
    asyncio.run(client.create_container(name, image, features))
    print(f"[green]✓[/green] Created container: [bold]{name}[/bold]")


@app.command()
def enter(name: str):
    """Enter a container shell."""
    import os
    cmd = asyncio.run(client.get_shell_command(name))
    os.execvp(cmd[0], cmd)


@app.command("list")
def list_containers():
    """List all containers."""
    containers = asyncio.run(client.list_containers())
    
    if not containers:
        print("No containers found. Create one with: [bold]kapsule create <name>[/bold]")
        return
    
    table = Table()
    table.add_column("Name", style="cyan")
    table.add_column("Status")
    table.add_column("Image")
    table.add_column("Created")
    
    for c in containers:
        status_style = "green" if c.status == "Running" else "dim"
        table.add_row(c.name, f"[{status_style}]{c.status}[/]", c.image, c.created)
    
    print(table)


@app.command()
def rm(
    name: str,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force removal")] = False,
):
    """Remove a container."""
    asyncio.run(client.delete_container(name, force))
    print(f"[green]✓[/green] Removed container: [bold]{name}[/bold]")


if __name__ == "__main__":
    app()
```

---

## Incus REST Client

```python
# src/kapsule/incus/client.py
from typing import Any
import httpx

class IncusClient:
    """Low-level client for Incus REST API over Unix socket."""
    
    def __init__(self, socket_path: str = "/var/lib/incus/unix.socket"):
        transport = httpx.HTTPTransport(uds=socket_path)
        self._client = httpx.Client(
            transport=transport,
            base_url="http://localhost",
            timeout=30.0,
        )
    
    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        """Make request and handle Incus response format."""
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        data = response.json()
        
        # Incus wraps responses in {"type": "sync/async", "metadata": ...}
        if data.get("type") == "error":
            raise IncusError(data.get("error", "Unknown error"))
        
        return data.get("metadata", data)
    
    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)
    
    def post(self, path: str, json: dict = None) -> dict[str, Any]:
        return self._request("POST", path, json=json)
    
    def put(self, path: str, json: dict = None) -> dict[str, Any]:
        return self._request("PUT", path, json=json)
    
    def delete(self, path: str) -> dict[str, Any]:
        return self._request("DELETE", path)


# src/kapsule/incus/containers.py
from dataclasses import dataclass
from .client import IncusClient

@dataclass
class Container:
    name: str
    status: str
    image: str
    created: str
    profiles: list[str]

class ContainerManager:
    """High-level container operations."""
    
    def __init__(self, client: IncusClient):
        self._client = client
    
    def list(self) -> list[Container]:
        """List all containers."""
        instances = self._client.get("/1.0/instances")
        containers = []
        for path in instances:
            name = path.split("/")[-1]
            info = self._client.get(f"/1.0/instances/{name}")
            containers.append(Container(
                name=name,
                status=info["status"],
                image=info.get("config", {}).get("image.description", "unknown"),
                created=info["created_at"],
                profiles=info.get("profiles", []),
            ))
        return containers
    
    def create(self, name: str, image: str, profiles: list[str]) -> Container:
        """Create a new container."""
        self._client.post("/1.0/instances", json={
            "name": name,
            "source": {
                "type": "image",
                "alias": image,
            },
            "profiles": profiles,
        })
        # Wait for creation, then return info
        return self.get(name)
    
    def get(self, name: str) -> Container:
        """Get container info."""
        info = self._client.get(f"/1.0/instances/{name}")
        return Container(
            name=name,
            status=info["status"],
            image=info.get("config", {}).get("image.description", "unknown"),
            created=info["created_at"],
            profiles=info.get("profiles", []),
        )
    
    def start(self, name: str) -> None:
        """Start a container."""
        self._client.put(f"/1.0/instances/{name}/state", json={
            "action": "start",
        })
    
    def stop(self, name: str, force: bool = False) -> None:
        """Stop a container."""
        self._client.put(f"/1.0/instances/{name}/state", json={
            "action": "stop",
            "force": force,
        })
    
    def delete(self, name: str) -> None:
        """Delete a container."""
        self._client.delete(f"/1.0/instances/{name}")
```

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

### Phase 1: Core Python Package ✅ (Partial)
- [x] Project structure with CMake build
- [x] Basic CLI scaffolding (typer-based with create, enter, list, rm, start, stop)
- [x] Python package structure (`src/cli/`)
- [x] Launcher script with vendored dependencies support
- [ ] Incus REST client (`httpx` + Unix socket)
- [ ] Container CRUD operations (actual implementation)
- [ ] Test against real Incus instance

### Phase 2: C++ Library ✅ (Partial)
- [x] `libkapsule-qt` library structure
- [x] `Container` data class with Qt property system
- [x] `KapsuleClient` D-Bus wrapper class (skeleton)
- [x] CMake build with ECM integration
- [x] Generated export headers and pkg-config
- [ ] Actual D-Bus communication implementation
- [ ] Connect to running daemon

### Phase 3: D-Bus Service
- [ ] D-Bus daemon with `dbus-next`
- [ ] Polkit integration for authorization
- [ ] CLI talks to daemon instead of Incus directly
- [ ] Systemd service file

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
- [dbus-next (Python D-Bus)](https://python-dbus-next.readthedocs.io/)
- [httpx (Python HTTP client)](https://www.python-httpx.org/)
