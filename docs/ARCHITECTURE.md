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
├── container_options.py # Option schema, validation, ContainerOptions
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
GetCreateSchema() -> str  # JSON option schema for CreateContainer
CreateContainer(name: str, image: str, options: a{sv}) -> object_path
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
        const ContainerOptions &options = {},
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
ContainerOptions opts;
opts.hostRootfs = false;
opts.mountHome = false;
opts.customMounts = {"/opt/data", "/srv/builds"};
co_await client.createContainer("dev", "ubuntu:24.04", opts,
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

### Terminal Container Detection (OSC 777)

`kapsule enter` emits OSC 777 markers so compatible terminals can track container context:

- Enter marker: `ESC ] 777 ; container ; push ; <container> ; kapsule BEL`
- Exit marker: `ESC ] 777 ; container ; pop ; ; BEL`

Implementation details:
- Markers are only emitted when stdout is a TTY.
- The push marker is emitted in the forked child just before `execvp(...)`.
- The pop marker is emitted by the parent after `waitpid(...)` returns.

This mirrors the lifecycle of the entered session while avoiding push/pop emission on fork failure.

---

## Container Creation Options (Schema-Driven)

Container creation is driven by a **Kapsule option schema** — a purpose-built
format that serves as the single source of truth for the daemon, CLI, and
any future GUI (KCM).

### Why Not Positional D-Bus Parameters?

D-Bus method parameters are positional and have no default values at the
wire level.  Adding a parameter changes the method signature, which is an
ABI break — the daemon and all clients must update in lockstep.  With a
growing set of options (session mode, host mounts, GPU, NVIDIA drivers,
home directory, custom mounts, …) this becomes unsustainable.

Instead, `CreateContainer` accepts a stable `a{sv}` (variant dict):

```
CreateContainer(name: s, image: s, options: a{sv}) → o
```

Clients send only the keys they care about; the daemon fills defaults for
the rest.  New options can be added without any D-Bus signature change.

### Why Not JSON Schema?

JSON Schema is a powerful validation spec, but it's a poor fit here:
- **Unordered**: `properties` is an object — no display order for forms.
- **No grouping**: no concept of UI sections.
- **No UI hints**: `type: "string"` says nothing about directory pickers.
- **Heavyweight**: the full spec includes `$ref`, `allOf`/`anyOf`, etc.
  — Kapsule would use 5% and carry the weight of the other 95%.
- **Expectations**: calling it "JSON Schema" invites bug reports for
  unsupported features.

Kapsule uses its own small format that borrows familiar vocabulary
(`type`, `title`, `description`, `default`, `items`) but is
unambiguously purpose-built.

### Schema Format

The schema is returned by `GetCreateSchema()` as a JSON string.

#### Top-Level Structure

```json
{
  "version": 1,
  "sections": [ ... ]
}
```

`version` is bumped when the schema format itself changes (not when
options are added — adding options is always backwards-compatible).

#### Section

An ordered group of related options. The order defines UI layout.

```json
{
  "id": "mounts",
  "title": "Host Mounts",
  "options": [ ... ]
}
```

| Field     | Type   | Description                        |
|-----------|--------|------------------------------------|
| `id`      | string | Stable identifier for the section  |
| `title`   | string | Human-readable heading             |
| `options` | array  | Ordered list of option descriptors |

#### Option Descriptor

```json
{
  "key": "mount_home",
  "type": "boolean",
  "title": "Mount Home Directory",
  "description": "Mount the user's home directory in the container",
  "default": true
}
```

| Field         | Type   | Required | Description                                    |
|---------------|--------|----------|------------------------------------------------|
| `key`         | string | yes      | The key used in the `a{sv}` dict               |
| `type`        | string | yes      | `"boolean"`, `"string"`, or `"array"`          |
| `title`       | string | yes      | Short label for UI rendering                   |
| `description` | string | yes      | Longer explanatory text                        |
| `default`     | varies | yes      | Value used when the key is omitted             |
| `items`       | object | array    | Element descriptor for `"array"` types         |
| `requires`    | object | no       | Inter-field dependency (see below)             |

#### Array Items

When `type` is `"array"`, the `items` field describes the element type:

```json
{
  "type": "string",
  "format": "directory-path"
}
```

The `format` field is a UI hint — `"directory-path"` tells a GUI to show
a directory picker.

#### Inter-Field Dependencies (`requires`)

```json
{
  "key": "nvidia_drivers",
  "type": "boolean",
  "default": true,
  "requires": {"gpu": true}
}
```

When `requires` is present, the option is only valid when **all** listed
prerequisites have the specified value.  UIs should disable/hide the
control when the prerequisite is not met.  The daemon enforces this
server-side as well.

### Schema Consumers

| Consumer         | How it uses the schema                              |
|------------------|-----------------------------------------------------|
| **Daemon**       | `parse_options()` validates `a{sv}` against it      |
| **CLI**          | Manual `--flag` mapping today (could be automated)  |
| **KCM** (future) | `Repeater` over sections → Kirigami `FormCard` delegates |
| **QML**          | `type` → widget: `boolean` → `SwitchDelegate`, `array` → list + file dialog |

### Data Flow

```
 Client                         D-Bus                          Daemon
───────                        ─────                          ──────
GetCreateSchema()  ──────────────────────────────►  returns JSON string
                                                    (from CREATE_SCHEMA)

  ◄── JSON schema ──────────────────────────────

  Render UI / --help from schema

  User sets: mount_home=false,
             custom_mounts=["/opt"]

  Build a{sv}: {"mount_home": false,             CreateContainer(name,
                "custom_mounts": ["/opt"]}  ────► image, options)
                                                       │
                                                  parse_options(raw)
                                                       │ rejects unknown keys
                                                       │ fills defaults
                                                       │ type-checks values
                                                       │ enforces constraints
                                                       ▼
                                                  ContainerOptions(
                                                    mount_home=False,
                                                    custom_mounts=["/opt"],
                                                    # everything else = default
                                                  )
```

### Adding a New Option

1. Add an entry to `CREATE_SCHEMA` in `container_options.py`.
2. Add a matching field to `ContainerOptions` (Python dataclass).
3. Handle the field in `container_service.py`.
4. Add the field to `Kapsule::ContainerOptions` in `types.h` and
   `toVariantMap()` in `types.cpp`.
5. Optionally add a CLI `--flag` in `main.cpp`.
6. **No D-Bus signature change.  No introspection XML regeneration.**

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
- **kapsule-home-{user}**: User's home directory bind-mount (unless `mount_home=false`)
- **kapsule-mount-{name}**: Custom directory bind-mounts from `custom_mounts` option

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
