<!--
SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Kapsule Architecture

Kapsule is an Incus-based container manager with native KDE/Plasma integration, designed for KDE Linux. It provides a distrobox-like experience with emphasis on nested containerization and seamless desktop integration.

## Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         User Applications (C++)                              │
│                                                                              │
│   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────────┐  │
│   │  kapsule CLI    │    │    Konsole      │    │ KCM / KIO (planned)     │  │
│   │  (main.cpp)     │    │  Integration    │    │                         │  │
│   └────────┬────────┘    └────────┬────────┘    └────────────┬────────────┘  │
│            │                      │                          │               │
│            └──────────────────────┼──────────────────────────┘               │
│                                   │                                          │
│                          ┌────────▼────────┐                                 │
│                          │  libkapsule-qt  │                                 │
│                          │  KapsuleClient  │                                 │
│                          └────────┬────────┘                                 │
└───────────────────────────────────┼──────────────────────────────────────────┘
                                    │ D-Bus (system bus)
                                    │ org.kde.kapsule
┌───────────────────────────────────▼─────────────────────────────────────────┐
│                        kapsule-daemon (Python)                              │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ org.kde.kapsule.Manager                                             │   │
│   │ ├── Properties: Version                                             │   │
│   │ └── Methods: CreateContainer, DeleteContainer, StartContainer, ...  │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ org.kde.kapsule.Operation (per-operation objects)                   │   │
│   │ Path: /org/kde/kapsule/operations/{id}                              │   │
│   │ ├── Properties: Id, Type, Description, Target, Status               │   │
│   │ ├── Signals: Message, ProgressStarted, ProgressUpdate, ...          │   │
│   │ └── Methods: Cancel                                                 │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│   ┌─────────────────┐    ┌─────────────────┐    ┌────────────────────────┐  │
│   │ ContainerService│──▶│  IncusClient    │    │  OperationTracker      │  │
│   │ (operations)    │    │  (REST client)  │    │  (D-Bus objects)       │  │
│   └─────────────────┘    └────────┬────────┘    └────────────────────────┘  │
└───────────────────────────────────┼─────────────────────────────────────────┘
                                    │ HTTP over Unix socket
                                    │ /var/lib/incus/unix.socket
┌───────────────────────────────────▼──────────────────────────────────────────┐
│                            Incus Daemon                                      │
│                    (container lifecycle, images, storage)                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Component Summary

| Component | Language | Purpose |
|-----------|----------|---------|
| `kapsule` CLI | C++ | User-facing command-line interface |
| `libkapsule-qt` | C++ | Qt/QCoro library for D-Bus communication |
| `kapsule-daemon` | Python | System service bridging D-Bus and Incus |
| Konsole Integration | C++/QML | Terminal container integration (planned) |
| KCM Module | QML/C++ | System Settings integration (planned) |

---

## kapsule-daemon (Python)

The daemon is the heart of Kapsule. It runs as a systemd system service (`kapsule-daemon.service`) and provides container management over D-Bus.

### Why Python?

- **Incus REST API** is trivial to consume with `httpx` async HTTP client
- **Fast iteration** during development
- **No CGO/native binding complexity** - pure HTTP over Unix socket
- **dbus-fast** provides excellent async D-Bus support with Cython acceleration

### Module Structure

```
src/daemon/
├── __main__.py          # Entry point: python -m kapsule.daemon
├── service.py           # KapsuleManagerInterface (D-Bus service)
├── container_service.py # Container lifecycle operations
├── operations.py        # @operation decorator, progress reporting
├── incus_client.py      # Typed async Incus REST client
├── models_generated.py  # Pydantic models from Incus OpenAPI spec
├── config.py            # User configuration handling
└── dbus_types.py        # D-Bus type annotations
```

### D-Bus Interface Design

The daemon exposes two interface types:

#### Manager Interface (`org.kde.kapsule.Manager`)

Singleton service at `/org/kde/kapsule`:

```python
# Methods - return operation object path immediately
CreateContainer(name: str, image: str, ...) -> object_path
DeleteContainer(name: str, force: bool) -> object_path
StartContainer(name: str) -> object_path
StopContainer(name: str, force: bool) -> object_path

# Properties
Version: str
```

#### Operation Interface (`org.kde.kapsule.Operation`)

Per-operation objects at `/org/kde/kapsule/operations/{id}`:

```python
# Properties
Id: str          # Unique operation identifier
Type: str        # "create", "delete", "start", "stop", etc.
Target: str      # Usually container name
Status: str      # "running", "completed", "failed", "cancelled"

# Progress signals
Message(type: int, message: str, indent: int)
ProgressStarted(id, description, total, indent)
ProgressUpdate(id, current, rate)
ProgressCompleted(id, success, message)
Completed(success: bool, error_message: str)

# Methods
Cancel()
```

### Operation Decorator Pattern

All long-running operations use the `@operation` decorator:

```python
@operation(
    "create",
    description="Creating container: {name}",
    target_param="name",
)
async def create_container(
    self,
    progress: OperationReporter,  # Auto-injected
    *,
    name: str,
    image: str,
) -> None:
    progress.info(f"Image: {image}")
    # ... do work ...
    progress.success(f"Container '{name}' created")
```

The decorator:
1. Creates an `OperationInterface` D-Bus object
2. Exports it at `/org/kde/kapsule/operations/{id}`
3. Returns the object path immediately to the caller
4. Runs the operation async in the background
5. Emits progress signals as work progresses
6. Cleans up the object when done

### Caller Credential Handling

The daemon identifies callers via D-Bus:

```python
async def _get_caller_credentials(self, sender: str) -> tuple[int, int, int]:
    """Get UID, GID, PID of D-Bus caller."""
    # Query org.freedesktop.DBus for caller identity
    # Read /proc/{pid}/status for GID
    # Read /proc/{pid}/environ for environment
```

This allows the daemon to:
- Set up user accounts in containers with matching UID/GID
- Pass through caller's environment variables
- Mount caller's home directory

---

## libkapsule-qt (C++)

A Qt6 library providing async D-Bus communication using QCoro coroutines.

### Key Classes

#### KapsuleClient

Main entry point for container management:

```cpp
class KapsuleClient : public QObject {
    // Async coroutine API
    QCoro::Task<QList<Container>> listContainers();
    QCoro::Task<Container> container(const QString &name);
    
    QCoro::Task<OperationResult> createContainer(
        const QString &name,
        const QString &image,
        ContainerMode mode = ContainerMode::Default,
        bool hostRootfs = true,
        ProgressHandler progress = {});
    
    QCoro::Task<EnterResult> prepareEnter(
        const QString &containerName = {},
        const QStringList &command = {});
    
    // ...
};
```

#### Container

Implicitly-shared value class representing a container:

```cpp
class Container {
    Q_GADGET
    Q_PROPERTY(QString name READ name)
    Q_PROPERTY(State state READ state)
    Q_PROPERTY(QString image READ image)
    Q_PROPERTY(ContainerMode mode READ mode)
    // ...
};
```

#### Progress Handling

Callbacks receive progress from operation D-Bus signals:

```cpp
using ProgressHandler = std::function<void(MessageType, const QString &, int)>;

// Usage
co_await client.createContainer("dev", "ubuntu:24.04",
    ContainerMode::Default, true,
    [](MessageType type, const QString &msg, int indent) {
        // Display progress to user
    });
```

---

## kapsule CLI (C++)

The CLI is a thin layer over `libkapsule-qt`, handling argument parsing and terminal output.

### Commands

| Command | Description |
|---------|-------------|
| `init` | Initialize Incus (one-time, runs as root) |
| `create <name>` | Create a new container |
| `enter [name]` | Enter a container (interactive shell) |
| `list` | List containers |
| `start <name>` | Start a stopped container |
| `stop <name>` | Stop a running container |
| `rm <name>` | Remove a container |
| `config` | Show configuration |

### Terminal Output

Uses [rang.hpp](src/cli/rang.hpp) for colored terminal output and a custom `Output` class for consistent formatting:

```cpp
auto &o = out();
o.info("Creating container...");
o.success("Container created!");
o.error("Something went wrong");
o.hint("Is the daemon running? Try: systemctl status kapsule-daemon");
```

---

## Container Configuration

Kapsule applies configuration directly to each container at creation time
(rather than via a shared Incus profile) so that changes to defaults never
affect existing containers.

### Security Settings
```yaml
security.privileged: "true"   # Required for nested containers
security.nesting: "true"      # Enable Docker/Podman inside
raw.lxc: "lxc.net.0.type=none"  # Host networking
```

### Devices
- **root**: Container root filesystem
- **gpu**: GPU passthrough for graphics (device nodes: `/dev/nvidia*`, `/dev/dri/*`)
- **hostfs** (default): Host filesystem at `/.kapsule/host` (for tooling access)

### NVIDIA GPU Support

NVIDIA GPUs require two things inside the container: **device nodes** and
**userspace driver libraries**.  Incus's `gpu` device type handles the first;
for the second, Kapsule registers an LXC mount hook
(`data/nvidia-container-hook.sh`) that calls `nvidia-container-cli configure`
to bind-mount the host's driver stack into the container rootfs before
`pivot_root`.

> **Why not use Incus's built-in `nvidia.runtime=true`?**
>
> Upstream Incus explicitly rejects `nvidia.runtime` on privileged containers,
> and the upstream LXC hook (`/usr/share/lxc/hooks/nvidia`) refuses to run
> outside a user namespace.  Both guards exist because `libnvidia-container`'s
> `--user` mode depends on user-namespace UID/GID remapping, and its default
> codepath tries to manage cgroups for device isolation — neither of which is
> relevant to privileged containers.
>
> Kapsule sidesteps this by invoking `nvidia-container-cli` with `--no-cgroups`
> (privileged containers already have unrestricted device access) and
> `--no-devbind` (Incus's `gpu` device already passes device nodes through).
> This reduces the tool's job to pure library injection, which works
> regardless of namespace or cgroup context.

The hook silently exits 0 when `nvidia-container-cli` or `/dev/nvidia0` is
absent, making it safe to register unconditionally on non-NVIDIA hosts.
Users can opt out at container creation time with `--no-nvidia-drivers`.
- Minimal mode (`--no-host-rootfs`): replaces `hostfs` with targeted mounts added
  during user setup:
  - `kapsule-hostrun-<uid>`: `/run/user/<uid>` at `/.kapsule/host/run/user/<uid>`
  - `kapsule-x11`: `/tmp/.X11-unix` at `/.kapsule/host/tmp/.X11-unix`

### User Setup

When entering a container, Kapsule:
1. Creates matching user account (same UID/GID)
2. Mounts home directory from host
3. If minimal mode: adds per-user host runtime and X11 mounts
4. Sets up environment variables (DISPLAY, XDG_*, etc.)
5. Configures shell and working directory

---

## System Integration

### D-Bus Configuration

`/usr/share/dbus-1/system.d/org.kde.kapsule.conf`:
- Root owns the service name
- All users can call methods (Polkit handles authorization)

### Systemd Units

```
kapsule-daemon.service     # Main daemon (Type=dbus)
```

Plus drop-in configurations for Incus:
- Socket permissions for user access
- Log directory setup

### Configuration File

Allows distros to set default images

`/etc/kapsule.conf` or `/usr/lib/kapsule.conf`:
```ini
[kapsule]
default_container = mydev
default_image = images:archlinux
```

---

## Future Work

- **Konsole Integration** - Container profiles and quick-access in terminal
- **KCM Module** - System Settings integration  
- **KIO Worker** - File manager integration
- **Polkit Integration** - Fine-grained authorization
- **Session Mode** - Container-local D-Bus with host forwarding
