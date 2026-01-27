# incusbox - Incus-based Distrobox Alternative

A distrobox-like tool using Incus as the container/VM backend, with native KDE/Plasma integration.

## Project Goals

1. **Primary:** Create containers that can run docker/podman inside them (nested containerization)
2. **Secondary:** Tight integration with KDE/Plasma (widget, KIO worker, System Settings module)
3. **Long-term:** Full distrobox feature parity with Incus backend

## Current Status

### Phase 1: Prototype (IN PROGRESS)

**Objective:** Create a privileged Arch Linux container via Incus with nested container support.

**Prerequisites:**
- Incus installed and initialized (`incus admin init`)
- User added to `incus-admin` group

**Note:** Using privileged containers for v1 to simplify nested container support. Security hardening deferred to v2.

**Packages to install inside container:**
- Base: base, base-devel, systemd, dbus
- Container runtime: podman, buildah, skopeo, fuse-overlayfs, slirp4netns, crun, netavark, aardvark-dns, passt
- User namespaces: shadow (newuidmap/newgidmap)
- Networking: iptables, nftables, iproute2

### Setup Commands

```bash
# Launch a privileged Arch Linux container with host networking
incus launch images:archlinux arch-container -c security.privileged=true -c raw.lxc="lxc.net.0.type=none"

# Or create without starting
incus init images:archlinux arch-container -c security.privileged=true -c raw.lxc="lxc.net.0.type=none"

# Start and enter the container
incus start arch-container
incus exec arch-container -- bash

# Install packages inside the container
pacman -Syu podman buildah skopeo fuse-overlayfs slirp4netns crun netavark aardvark-dns passt
```

### Test nested containers (inside Incus container)

```bash
podman run --rm alpine echo "hello from nested container"
podman run --rm docker.io/hello-world
```

## Known Issues / TODO

### Immediate (Phase 1)
- [ ] Install and initialize Incus on host
- [ ] Launch privileged Arch container
- [ ] Verify nested podman works inside the container
- [ ] Test overlayfs storage driver (native, since privileged)

### Phase 2: CLI Tools
- [ ] `incusbox-create` - Create containers from various distro images
- [ ] `incusbox-enter` - Enter containers (wrapper around incus exec)
- [ ] `incusbox-export` - Export applications to host (modify .desktop files)
- [ ] `incusbox-rm` - Remove containers
- [ ] `incusbox-list` - List containers with status

### Phase 3: Profile Management
- [ ] Design composable profile system
- [ ] Create base profiles for common features
- [ ] Implement profile generation in CLI tools

### Phase 4: KDE Integration
- [ ] D-Bus service (`org.kde.incusbox`) for container lifecycle
- [ ] Plasma widget showing container status and quick actions
- [ ] KIO worker (`incusbox://container/path`) for Dolphin integration
- [ ] KCM System Settings module for configuration

### Phase 5: Advanced Features
- [ ] VM support via Incus VMs for stronger isolation
- [ ] Home directory integration (like distrobox)
- [ ] GPU acceleration (bind GPU device)

## Profile Management Strategy

### Composable Profiles

Use Incus's native profile stacking to combine features. Each profile handles one concern:

```
┌────────────────────────────────────────────────────────────┐
│                    Container Instance                       │
├────────────────────────────────────────────────────────────┤
│  default + incusbox-base + graphics + audio + home + gpu   │
└────────────────────────────────────────────────────────────┘
```

### Profile Definitions

Store profiles in `~/.config/incusbox/profiles/` or `/etc/incusbox/profiles/`:

**incusbox-base** (always applied)
```yaml
config:
  security.privileged: "true"
  raw.lxc: |
    lxc.net.0.type=none
```

**incusbox-graphics** (Wayland + X11 fallback)
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
  xauth:
    type: disk
    source: "${XAUTHORITY}"
    path: "/run/user/1000/.Xauthority"
```

**incusbox-audio** (PipeWire/PulseAudio)
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

**incusbox-dbus** (session bus access)
```yaml
config:
  environment.DBUS_SESSION_BUS_ADDRESS: "unix:path=/run/user/1000/bus"
devices:
  dbus:
    type: disk
    source: "${XDG_RUNTIME_DIR}/bus"
    path: "/run/user/1000/bus"
```

**incusbox-home** (home directory mount)
```yaml
devices:
  home:
    type: disk
    source: "${HOME}"
    path: "${HOME}"
```

**incusbox-gpu** (GPU passthrough)
```yaml
devices:
  gpu:
    type: gpu
    gid: "video"
```

### CLI Usage

```bash
# Create container with specific features
incusbox create arch-dev --graphics --audio --dbus --home

# This translates to:
incus launch images:archlinux arch-dev \
  --profile default \
  --profile incusbox-base \
  --profile incusbox-graphics \
  --profile incusbox-audio \
  --profile incusbox-dbus \
  --profile incusbox-home

# Or use presets
incusbox create arch-dev --preset desktop  # graphics + audio + dbus + home + gpu
incusbox create arch-dev --preset minimal  # base only
incusbox create arch-dev --preset server   # base + dbus
```

### Profile Installation

On first run, `incusbox init` registers all profiles with Incus:

```bash
incusbox init
# Creates: incusbox-base, incusbox-graphics, incusbox-audio, etc.
# Stores user config in ~/.config/incusbox/config.yaml
```

### Variable Expansion

Profiles use environment variable placeholders that get expanded at container creation time:
- `${HOME}` → `/home/fernie`
- `${XDG_RUNTIME_DIR}` → `/run/user/1000`
- `${WAYLAND_DISPLAY}` → `wayland-0`
- `${DISPLAY}` → `:0`

The CLI tool handles this expansion before passing to Incus.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      User Interface                      │
├──────────────┬──────────────┬──────────────┬────────────┤
│ CLI Tools    │ Plasma Widget│ KCM Module   │ KIO Worker │
│ (incusbox)   │ (status/mgmt)│ (settings)   │ (file acc) │
└──────────────┴──────────────┴──────────────┴────────────┘
                              │
┌─────────────────────────────────────────────────────────┐
│                     D-Bus Service                        │
│                    org.kde.incusbox                      │
└─────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────┐
│                    Core Library                          │
│  - Container creation/management                         │
│  - Profile configuration                                 │
│  - Bind mount configuration                              │
│  - Application export                                    │
└─────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────┐
│                         Incus                            │
│              (incus CLI / REST API / liblxc)             │
└─────────────────────────────────────────────────────────┘
```

## Key Technical Details

### Nested Container Requirements (v1 - Privileged)

For podman/docker inside privileged Incus containers:

1. **security.privileged: "true"** - container runs with full root privileges
2. Native overlayfs works directly (no fuse-overlayfs needed)
3. All capabilities available, no syscall filtering

**Security Note:** Privileged containers have no isolation from the host kernel. This is acceptable for development/trusted workloads.

### Storage Driver

Podman inside privileged Incus containers can use native **overlayfs** directly. No special configuration needed.

### Comparison with Distrobox

| Feature | Distrobox | incusbox (planned) |
|---------|-----------|---------------------|
| Backend | podman/docker | Incus (containers/VMs) |
| Init system | None (container) | Full systemd |
| Isolation | Container namespaces | Container or VM |
| Desktop integration | Generic XDG | KDE-native |
| Management | Custom scripts | incus CLI + custom |
| Image source | Container registries | Incus images + custom |

## References

- [Incus documentation](https://linuxcontainers.org/incus/docs/main/)
- [Incus images](https://images.linuxcontainers.org/)
- [Distrobox source](https://github.com/89luca89/distrobox)
- [Arch Wiki: Incus](https://wiki.archlinux.org/title/Incus)
