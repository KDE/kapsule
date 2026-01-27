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
| **Python CLI** | Same codebase as daemon. `click` for argument parsing. Instant development velocity. |

### Component Languages

| Component | Language | Build System | Framework |
|-----------|----------|--------------|-----------|
| `kapsule` CLI | Python 3.11+ | meson + setuptools | click |
| `kapsule-daemon` | Python 3.11+ | meson + setuptools | dbus-next, httpx |
| `libkapsule-qt` | C++ | CMake | Qt6, KF6 |
| Plasma Widget | QML | CMake | libplasma |
| KIO Worker | C++ | CMake | KIO |
| KCM Module | QML + C++ | CMake | KDeclarative |

### Python Dependencies

```
# Core
httpx           # Async HTTP client with Unix socket support
dbus-next       # Modern async D-Bus library  
click           # CLI framework
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
├── meson.build                     # Top-level build (coordinates Python + C++)
├── pyproject.toml                  # Python package definition
│
├── src/
│   └── kapsule/                    # Python package
│       ├── __init__.py
│       ├── cli/                    # CLI commands
│       │   ├── __init__.py
│       │   ├── main.py             # Entry point
│       │   ├── create.py
│       │   ├── enter.py
│       │   ├── list.py
│       │   └── rm.py
│       ├── daemon/                 # D-Bus service
│       │   ├── __init__.py
│       │   ├── service.py          # org.kde.kapsule implementation
│       │   └── polkit.py           # Authorization helpers
│       ├── incus/                  # Incus REST client
│       │   ├── __init__.py
│       │   ├── client.py           # HTTP client wrapper
│       │   ├── containers.py       # Container operations
│       │   ├── images.py           # Image operations
│       │   └── profiles.py         # Profile operations
│       ├── features/               # Feature → profile mapping
│       │   ├── __init__.py
│       │   ├── registry.py         # Feature definitions
│       │   └── resolver.py         # --with/--without logic
│       └── config.py               # Configuration handling
│
├── kde/                            # KDE/Qt components (C++)
│   ├── CMakeLists.txt
│   ├── libkapsule-qt/              # Qt wrapper around D-Bus
│   │   ├── CMakeLists.txt
│   │   ├── kapsuleclient.h
│   │   ├── kapsuleclient.cpp
│   │   └── container.h
│   ├── kio/                        # KIO worker
│   │   ├── CMakeLists.txt
│   │   └── kio_kapsule.cpp
│   ├── kcm/                        # System Settings module
│   │   ├── CMakeLists.txt
│   │   ├── kcm_kapsule.cpp
│   │   └── ui/
│   │       └── main.qml
│   └── plasmoid/                   # Plasma widget
│       ├── CMakeLists.txt
│       └── package/
│           ├── metadata.json
│           └── contents/
│               └── ui/
│                   └── main.qml
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
├── scripts/
│   ├── kapsule-firstboot.sh        # First-boot initialization
│   └── container-setup.sh          # Runs inside new containers
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
# Custom project: Kapsule
project kapsule:
  repository: https://invent.kde.org/utilities/kapsule.git
  branch: main
  # Meson handles Python, CMake handles C++/Qt parts
  override-build-system: meson
  meson-options: -Dkde_components=true

# KDE dependencies needed for the Qt components
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

### Meson Build Configuration

```meson
# meson.build
project('kapsule', 
  version: '0.1.0',
  meson_version: '>= 1.0.0',
)

# Python components
python = import('python')
py = python.find_installation('python3', required: true)

# Install Python package
py.install_sources(
  'src/kapsule/__init__.py',
  # ... all Python files
  subdir: 'kapsule',
)

# Install CLI entry point
install_data('scripts/kapsule', install_dir: get_option('bindir'))

# Install data files
install_subdir('data/profiles', install_dir: get_option('datadir') / 'kapsule')
install_data('data/dbus/org.kde.kapsule.service', 
  install_dir: get_option('datadir') / 'dbus-1/system-services')
install_data('data/polkit/org.kde.kapsule.policy',
  install_dir: get_option('datadir') / 'polkit-1/actions')
install_data('data/systemd/kapsule-daemon.service',
  install_dir: get_option('prefix') / 'lib/systemd/system')

# KDE components (optional, requires Qt/KF6)
if get_option('kde_components')
  subdir('kde')
endif
```

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

### Commands

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

### Example CLI Implementation

```python
# src/kapsule/cli/main.py
import click
import asyncio
from kapsule.daemon.client import KapsuleClient

@click.group()
@click.version_option()
@click.pass_context
def cli(ctx):
    """Kapsule - Incus-based container manager with KDE integration."""
    ctx.ensure_object(dict)
    ctx.obj['client'] = KapsuleClient()

@cli.command()
@click.argument('name')
@click.option('--image', '-i', default='archlinux', help='Base image')
@click.option('--with', 'with_features', multiple=True, help='Features to enable')
@click.option('--without', 'without_features', multiple=True, help='Features to disable')
@click.pass_context
def create(ctx, name, image, with_features, without_features):
    """Create a new container.
    
    By default, all features are enabled (graphics, audio, home, gpu).
    Use --without to disable specific features.
    """
    client = ctx.obj['client']
    features = resolve_features(with_features, without_features)
    asyncio.run(client.create_container(name, image, features))
    click.echo(f"✓ Created container: {name}")

@cli.command()
@click.argument('name')
@click.pass_context
def enter(ctx, name):
    """Enter a container shell."""
    import os
    client = ctx.obj['client']
    cmd = asyncio.run(client.get_shell_command(name))
    os.execvp(cmd[0], cmd)

@cli.command('list')
@click.pass_context
def list_containers(ctx):
    """List all containers."""
    client = ctx.obj['client']
    containers = asyncio.run(client.list_containers())
    
    if not containers:
        click.echo("No containers found. Create one with: kapsule create <name>")
        return
    
    click.echo(f"{'NAME':<20} {'STATUS':<10} {'IMAGE':<15} {'CREATED'}")
    click.echo("-" * 60)
    for c in containers:
        click.echo(f"{c.name:<20} {c.status:<10} {c.image:<15} {c.created}")

def main():
    cli(obj={})

if __name__ == '__main__':
    main()
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

### Phase 1: Core Python Package
- [ ] Project structure with meson build
- [ ] Incus REST client (`httpx` + Unix socket)
- [ ] Container CRUD operations
- [ ] Basic CLI (create, list, enter, rm)
- [ ] Test against real Incus instance

### Phase 2: D-Bus Service
- [ ] D-Bus daemon with `dbus-next`
- [ ] Polkit integration for authorization
- [ ] CLI talks to daemon instead of Incus directly
- [ ] Systemd service file

### Phase 3: Feature System
- [ ] Feature ↔ profile mapping
- [ ] Profile YAML loading and validation
- [ ] Variable expansion (`${HOME}`, `${XDG_RUNTIME_DIR}`, etc.)
- [ ] Profile registration with Incus

### Phase 4: KDE Components
- [ ] `libkapsule-qt` - D-Bus wrapper for Qt
- [ ] Plasma widget (container status, quick actions)
- [ ] KIO worker (`kapsule://container/path`)
- [ ] KCM System Settings module

### Phase 5: KDE Linux Integration
- [ ] First-boot service
- [ ] Pre-bundled container image
- [ ] Konsole integration (default to container)
- [ ] Seamless first-run experience

### Phase 6: Advanced Features
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
