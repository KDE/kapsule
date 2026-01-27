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
