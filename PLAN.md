# incusbox - Incus-based Distrobox Alternative

A distrobox-like tool using Incus as the container/VM backend, with native KDE/Plasma integration.

## Project Goals

1. **Primary:** Create containers that can run docker/podman inside them (nested containerization)
2. **Secondary:** Tight integration with KDE/Plasma (widget, KIO worker, System Settings module)
3. **Long-term:** Full distrobox feature parity with Incus backend

## Current Status

### Phase 1: Prototype (IN PROGRESS)

**Objective:** Create an Arch Linux container via Incus with nested container support.

**Prerequisites:**
- Incus installed and initialized (`incus admin init`)
- User added to `incus-admin` group

**Container profile for nested containers:**
```yaml
config:
  security.nesting: "true"
  security.syscalls.intercept.mknod: "true"
  security.syscalls.intercept.setxattr: "true"
devices:
  eth0:
    name: eth0
    network: incusbr0
    type: nic
  root:
    path: /
    pool: default
    type: disk
```

**Packages to install inside container:**
- Base: base, base-devel, systemd, dbus
- Container runtime: podman, buildah, skopeo, fuse-overlayfs, slirp4netns, crun, netavark, aardvark-dns, passt
- User namespaces: shadow (newuidmap/newgidmap)
- Networking: iptables, nftables, iproute2

### Setup Commands

```bash
# Create a profile for nested container support
incus profile create nested
incus profile set nested security.nesting=true
incus profile set nested security.syscalls.intercept.mknod=true
incus profile set nested security.syscalls.intercept.setxattr=true

# Launch an Arch Linux container with the nested profile
incus launch images:archlinux arch-container --profile default --profile nested

# Or create a container without starting it
incus init images:archlinux arch-container --profile default --profile nested

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
- [ ] Create nested container profile
- [ ] Verify nested podman works inside the container
- [ ] Test fuse-overlayfs storage driver
- [ ] Ensure subuid/subgid mappings work for rootless podman

### Phase 2: CLI Tools
- [ ] `incusbox-create` - Create containers from various distro images
- [ ] `incusbox-enter` - Enter containers (wrapper around incus exec)
- [ ] `incusbox-export` - Export applications to host (modify .desktop files)
- [ ] `incusbox-rm` - Remove containers
- [ ] `incusbox-list` - List containers with status

### Phase 3: KDE Integration
- [ ] D-Bus service (`org.kde.incusbox`) for container lifecycle
- [ ] Plasma widget showing container status and quick actions
- [ ] KIO worker (`incusbox://container/path`) for Dolphin integration
- [ ] KCM System Settings module for configuration

### Phase 4: Advanced Features
- [ ] VM support via Incus VMs for stronger isolation
- [ ] Home directory integration (like distrobox)
- [ ] Graphics passthrough (X11/Wayland)
- [ ] Audio passthrough (PipeWire/PulseAudio)
- [ ] GPU acceleration (bind GPU device)

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

### Nested Container Requirements

For podman/docker inside Incus:

1. **security.nesting: "true"** - enables nested container support
2. **security.syscalls.intercept.mknod: "true"** - allows mknod syscalls for device creation
3. **security.syscalls.intercept.setxattr: "true"** - allows extended attributes
4. **subuid/subgid:** Mappings in /etc/subuid and /etc/subgid inside container

### Storage Driver

Podman inside Incus uses **fuse-overlayfs** because native overlayfs doesn't work with user namespaces in nested scenarios. Configured in `/etc/containers/storage.conf`.

### Comparison with Distrobox

| Feature | Distrobox | incusbox (planned) |
|---------|-----------|---------------------|
| Backend | podman/docker | Incus (containers/VMs) |
| Init system | None (container) | Full systemd |
| Isolation | Container namespaces | Container or VM |
| Desktop integration | Generic XDG | KDE-native |
| Management | Custom scripts | incus CLI + custom |
| Image source | Container registries | Incus images + custom |

### Incus Advantages over systemd-nspawn

- Built-in image server with many distros pre-built
- Native VM support alongside containers
- REST API for programmatic control
- Clustering support for multi-host deployments
- Snapshot and backup functionality
- Network and storage management
- Web UI available (incus-ui-canonical)

## References

- [Incus documentation](https://linuxcontainers.org/incus/docs/main/)
- [Incus images](https://images.linuxcontainers.org/)
- [Distrobox source](https://github.com/89luca89/distrobox)
- [Arch Wiki: Incus](https://wiki.archlinux.org/title/Incus)
