# Kapsule Architecture

Kapsule is an Incus-based container manager with native KDE/Plasma integration, designed for KDE Linux. It provides a distrobox-like experience with emphasis on nested containerization and seamless desktop integration.

## Overview

```mermaid
flowchart TB
    subgraph UserApps["User Applications - C++"]
        CLI["kapsule CLI\n(main.cpp)"]
        Konsole["Konsole\nIntegration"]
        KCM["KCM / KIO\n(planned)"]
    end

    subgraph LibKapsule["libkapsule-qt"]
        KapsuleClient["KapsuleClient"]
    end

    CLI --> KapsuleClient
    Konsole --> KapsuleClient
    KCM --> KapsuleClient

    KapsuleClient -->|"D-Bus system bus\norg.kde.kapsule"| Daemon

    subgraph Daemon["kapsule-daemon - Python"]
        subgraph Manager["org.kde.kapsule.Manager"]
            ManagerProps["Properties: Version"]
            ManagerMethods["Methods: CreateContainer,\nDeleteContainer, StartContainer, ..."]
            ManagerSignals["Signals: OperationCreated,\nOperationRemoved"]
        end

        subgraph Operation["org.kde.kapsule.Operation\n/org/kde/kapsule/operations/id"]
            OpProps["Properties: Id, Type,\nDescription, Target, Status"]
            OpSignals["Signals: Message, ProgressStarted,\nProgressUpdate, ..."]
            OpMethods["Methods: Cancel"]
        end

        ContainerService["ContainerService\n(operations)"]
        IncusClient["IncusClient\n(REST client)"]
        OperationTracker["OperationTracker\n(D-Bus objects)"]

        ContainerService --> IncusClient
    end

    IncusClient -->|"HTTP over Unix socket\n/var/lib/incus/unix.socket"| Incus

    subgraph Incus["Incus Daemon"]
        IncusDesc["container lifecycle, images, storage"]
    end
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
├── profile.py           # Kapsule container profile definition
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

# Global signals (for monitoring tools)
OperationCreated(object_path, operation_type, target)
OperationRemoved(object_path)
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
    ContainerMode::Default,
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
o.hint("Try: kapsule init");
```

---

## Container Profile

Kapsule containers use a custom Incus profile (`kapsule-base`) that enables:

### Security Settings
```yaml
security.privileged: "true"   # Required for nested containers
security.nesting: "true"      # Enable Docker/Podman inside
raw.lxc: "lxc.net.0.type=none"  # Host networking
```

### Devices
- **root**: Container root filesystem
- **x11**: X11 socket passthrough (`/tmp/.X11-unix`)
- **gpu**: GPU passthrough for graphics
- **hostfs**: Host filesystem at `/.kapsule/host` (for tooling access)

### User Setup

When entering a container, Kapsule:
1. Creates matching user account (same UID/GID)
2. Mounts home directory from host
3. Sets up environment variables (DISPLAY, XDG_*, etc.)
4. Configures shell and working directory

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
