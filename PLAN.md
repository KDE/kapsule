# Kapsule - Incus-based Distrobox Alternative

A distrobox-like tool using Incus as the container/VM backend, with native KDE/Plasma integration. Ships with KDE Linux.

**CLI:** `kapsule` (alias: `kap`)

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
- [ ] `kapsule-create` - Create containers from various distro images
- [ ] `kapsule-enter` - Enter containers (wrapper around incus exec)
- [ ] `kapsule-export` - Export applications to host (modify .desktop files)
- [ ] `kapsule-rm` - Remove containers
- [ ] `kapsule-list` - List containers with status

### Phase 3: Profile Management
- [ ] Design composable profile system
- [ ] Create base profiles for common features
- [ ] Implement profile generation in CLI tools

### Phase 4: KDE Integration
- [ ] D-Bus service (`org.kde.kapsule`) for container lifecycle
- [ ] Plasma widget showing container status and quick actions
- [ ] KIO worker (`kapsule://container/path`) for Dolphin integration
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
│  default + kapsule-base + graphics + audio + home + gpu   │
└────────────────────────────────────────────────────────────┘
```

### Profile Definitions

Store profiles in `~/.config/kapsule/profiles/` or `/etc/kapsule/profiles/`:

**kapsule-base** (always applied)
```yaml
config:
  security.privileged: "true"
  raw.lxc: |
    lxc.net.0.type=none
```

**kapsule-graphics** (Wayland + X11 fallback)
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

**kapsule-audio** (PipeWire/PulseAudio)
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

**kapsule-dbus** (session bus access)
```yaml
config:
  environment.DBUS_SESSION_BUS_ADDRESS: "unix:path=/run/user/1000/bus"
devices:
  dbus:
    type: disk
    source: "${XDG_RUNTIME_DIR}/bus"
    path: "/run/user/1000/bus"
```

**kapsule-home** (home directory mount)
```yaml
devices:
  home:
    type: disk
    source: "${HOME}"
    path: "${HOME}"
```

**kapsule-gpu** (GPU passthrough)
```yaml
devices:
  gpu:
    type: gpu
    gid: "video"
```

### CLI Usage

```bash
# Create container with specific features
kapsule create arch-dev --graphics --audio --dbus --home
# Or using the short alias:
kap create arch-dev --graphics --audio --dbus --home

# This translates to:
incus launch images:archlinux arch-dev \
  --profile default \
  --profile kapsule-base \
  --profile kapsule-graphics \
  --profile kapsule-audio \
  --profile kapsule-dbus \
  --profile kapsule-home

# Or use presets
kap create arch-dev --preset desktop  # graphics + audio + dbus + home + gpu
kap create arch-dev --preset minimal  # base only
kap create arch-dev --preset server   # base + dbus
```

### Profile Installation

On first run, `kapsule init` registers all profiles with Incus:

```bash
kapsule init
# Creates: kapsule-base, kapsule-graphics, kapsule-audio, etc.
# Stores user config in ~/.config/kapsule/config.yaml
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
│ (kapsule)   │ (status/mgmt)│ (settings)   │ (file acc) │
└──────────────┴──────────────┴──────────────┴────────────┘
                              │
┌─────────────────────────────────────────────────────────┐
│                     D-Bus Service                        │
│                    org.kde.kapsule                      │
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

| Feature | Distrobox | kapsule (planned) |
|---------|-----------|---------------------|
| Backend | podman/docker | Incus (containers/VMs) |
| Init system | None (container) | Full systemd |
| Isolation | Container namespaces | Container or VM |
| Desktop integration | Generic XDG | KDE-native |
| Management | Custom scripts | incus CLI + custom |
| Image source | Container registries | Incus images + custom |

## KDE Linux Integration (Immutable Distro)

KDE Linux is an immutable distro where the root filesystem is read-only and atomic updates replace the entire system image. This creates a clear separation between what's "baked in" at image build time vs what happens at runtime.

### Build-Time (Baked into KDE Linux Image)

These components should be part of the KDE Linux image itself:

#### 1. **Core Incus/Container Infrastructure**
```
/usr/bin/incus              # Incus CLI
/usr/lib/incus/             # Incus daemon and tools
/usr/bin/kapsule            # Our CLI tool
/usr/bin/kap                # Symlink to kapsule (short alias)
/usr/lib/kapsule/           # Core library
```

#### 2. **Default Configuration (Read-Only)**
```
/usr/share/kapsule/
├── profiles/               # Default profile templates
│   ├── kapsule-base.yaml
│   ├── kapsule-graphics.yaml
│   ├── kapsule-audio.yaml
│   ├── kapsule-dbus.yaml
│   ├── kapsule-home.yaml
│   └── kapsule-gpu.yaml
├── images/                 # Pre-packaged container rootfs (optional)
│   └── arch-container.tar  # Compressed container image
├── presets/
│   ├── desktop.yaml        # graphics + audio + dbus + home + gpu
│   ├── minimal.yaml        # base only
│   └── server.yaml         # base + dbus
└── defaults.yaml           # Default settings
```

#### 3. **KDE Integration Components**
```
/usr/lib/systemd/user/org.kde.kapsule.service  # D-Bus service (user session)
/usr/share/dbus-1/services/org.kde.kapsule.service
/usr/share/plasma/plasmoids/org.kde.kapsule/   # Plasma widget
/usr/lib/qt6/plugins/kf6/kio/kapsule.so        # KIO worker
/usr/share/kpackage/kcms/kcm_kapsule/          # System Settings module
/usr/share/applications/org.kde.kapsule.desktop
```

#### 4. **Systemd Units (Templates)**
```
/usr/lib/systemd/system/incus.service           # Incus daemon
/usr/lib/systemd/system/incus.socket            # Socket activation
/usr/lib/tmpfiles.d/kapsule.conf               # Runtime directory setup
/usr/lib/sysusers.d/kapsule.conf               # incus-admin group creation
```

#### 5. **Polkit Rules**
```
/usr/share/polkit-1/actions/org.kde.kapsule.policy
/usr/share/polkit-1/rules.d/50-kapsule.rules
```

---

### First-Boot / System Setup (Runs Once After Install)

These happen on first boot via systemd-firstboot or a custom first-boot service:

#### 1. **Incus Initialization**
```bash
# Initialize Incus storage and networking
# This creates /var/lib/incus/ structure
incus admin init --minimal --auto

# Or with ZFS if available
incus admin init --storage-backend=zfs --storage-pool=incus
```

#### 2. **User Group Setup**
```bash
# Already handled by sysusers.d, but first user needs to be added
usermod -aG incus-admin $FIRST_USER
```

#### 3. **Import Pre-bundled Container Image (Optional)**
```bash
# If we ship a pre-built container image
if [[ -f /usr/share/kapsule/images/arch-container.tar ]]; then
    incus image import /usr/share/kapsule/images/arch-container.tar --alias kapsule/arch
fi
```

---

### Runtime (Per-User, On-Demand)

These happen when a user first runs kapsule or creates containers:

#### 1. **User Configuration (Writable)**
```
~/.config/kapsule/
├── config.yaml             # User preferences (default distro, presets, etc.)
├── profiles/               # User-defined custom profiles
└── containers.d/           # Per-container overrides
```

#### 2. **Incus Profile Registration**
```bash
# On first run, register profiles from /usr/share/kapsule/profiles/ + user profiles
# Profiles are registered with Incus (stored in /var/lib/incus/)
kapsule init  # Idempotent - checks if profiles exist first
```

#### 3. **Container Creation**
```bash
# Download image (if not pre-bundled) and create container
kapsule create arch-dev --preset desktop

# This:
# 1. Fetches images:archlinux (cached in /var/lib/incus/images/)
# 2. Creates container with merged profiles
# 3. Runs init script inside container
```

#### 4. **Session Integration**
```bash
# D-Bus service starts on demand via socket activation
# Plasma widget queries org.kde.kapsule for container status
# KIO worker mounts container filesystem on demand
```

---

### Data Locations on KDE Linux

| Path | Type | Purpose |
|------|------|---------|
| `/usr/share/kapsule/` | Immutable | Default profiles, presets, bundled images |
| `/etc/kapsule/` | Mutable (config) | System-wide overrides (admin can customize) |
| `/var/lib/incus/` | Mutable (state) | Incus storage pools, images, containers |
| `/var/lib/kapsule/` | Mutable (state) | Global cache, shared container data |
| `~/.config/kapsule/` | User config | Per-user preferences and custom profiles |
| `~/.local/share/kapsule/` | User data | Exported .desktop files, icons |

---

### First-Run Flow (User Perspective)

```
┌─────────────────────────────────────────────────────────────────┐
│  User clicks "Kapsule" in app launcher or Plasma widget        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Check: Is user in incus-admin group?                           │
│  NO → Prompt to add user (requires logout/login or newgrp)      │
│  YES → Continue                                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Check: Is Incus initialized? (/var/lib/incus/database exists?) │
│  NO → Run incus admin init (may need Polkit elevation)          │
│  YES → Continue                                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Check: Are kapsule profiles registered?                       │
│  NO → Run kapsule init (register profiles with Incus)          │
│  YES → Continue                                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Show container list (empty on first run)                       │
│  Offer: "Create your first container" wizard                    │
└─────────────────────────────────────────────────────────────────┘
```

---

### Pre-Bundled vs Downloaded Images

**Option A: Pre-bundle a base container image**
- Pros: Instant container creation, works offline, consistent experience
- Cons: Larger KDE Linux image size (+500MB-1GB), may get stale

**Option B: Download on first use**
- Pros: Smaller base image, always fresh, user chooses distro
- Cons: Requires network, slower first-run experience

**Recommendation: Hybrid Approach**
- Ship a minimal "kapsule-ready" Arch image (~300MB compressed) for instant first container
- Allow downloading other distros (Ubuntu, Fedora, etc.) on demand
- Cache downloaded images in `/var/lib/incus/images/`

---

### Systemd Integration for KDE Linux

**kapsule-firstboot.service** (runs once)
```ini
[Unit]
Description=Kapsule First Boot Setup
ConditionPathExists=!/var/lib/kapsule/.initialized
After=incus.service

[Service]
Type=oneshot
ExecStart=/usr/lib/kapsule/firstboot.sh
ExecStartPost=/usr/bin/touch /var/lib/kapsule/.initialized
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

**firstboot.sh**
```bash
#!/bin/bash
# Initialize Incus if not already done
if ! incus admin init --dump &>/dev/null; then
    incus admin init --minimal --auto
fi

# Import bundled container image
if [[ -f /usr/share/kapsule/images/arch-container.tar.zst ]]; then
    zstd -d /usr/share/kapsule/images/arch-container.tar.zst -o /tmp/arch.tar
    incus image import /tmp/arch.tar --alias kapsule/arch
    rm /tmp/arch.tar
fi

# Register default profiles
for profile in /usr/share/kapsule/profiles/*.yaml; do
    name=$(basename "$profile" .yaml)
    if ! incus profile show "$name" &>/dev/null; then
        incus profile create "$name"
        incus profile edit "$name" < "$profile"
    fi
done
```

---

### Summary: Build vs Runtime

| Component | Build Time | First Boot | First Run (User) | On Demand |
|-----------|:----------:|:----------:|:----------------:|:---------:|
| Incus binaries | ✓ | | | |
| kapsule CLI/lib | ✓ | | | |
| KDE integration (widget, KIO, KCM) | ✓ | | | |
| Default profile templates | ✓ | | | |
| Pre-bundled container image | ✓ | | | |
| Incus initialization | | ✓ | | |
| Profile registration with Incus | | ✓ | | |
| Image import to Incus | | ✓ | | |
| User added to incus-admin | | ✓* | | |
| User config (~/.config/kapsule) | | | ✓ | |
| Container creation | | | | ✓ |
| Image download (non-bundled) | | | | ✓ |

*First user added automatically; subsequent users via System Settings

---

## Seamless First-Run: Konsole Defaults to Kapsule

### The Challenge

If Konsole defaults to opening inside an kapsule container, the **entire dependency chain must succeed** on the very first terminal launch—with zero user intervention.

```
User clicks Konsole
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│ DEPENDENCY CHAIN (all must be true)                             │
├─────────────────────────────────────────────────────────────────┤
│ 1. Incus daemon running                                         │
│ 2. User authorized to use Incus (THE HARD ONE)                  │
│ 3. Incus storage/network initialized                            │
│ 4. Kapsule profiles registered                                 │
│ 5. Container image available                                    │
│ 6. Default container exists                                     │
│ 7. Container is running (or starts instantly)                   │
│ 8. User session inside container works                          │
└─────────────────────────────────────────────────────────────────┘
       │
       ▼
User sees shell prompt inside container
```

**If ANY step fails, user gets a broken terminal.** Unacceptable.

---

### Blocker Analysis & Mitigations

#### Blocker 1: Incus Daemon Not Running

**Problem:** Service could be disabled, crashed, or not yet started.

**Mitigation:**
- Use **socket activation** (`incus.socket`) - daemon starts on first connection
- Mark `incus.socket` as `WantedBy=sockets.target` (enabled by default)
- Konsole wrapper checks socket exists before attempting connection

```ini
# /usr/lib/systemd/system/incus.socket
[Socket]
ListenStream=/var/lib/incus/unix.socket

[Install]
WantedBy=sockets.target
```

**Fallback:** If socket doesn't exist after 2s, fall back to host shell.

---

#### Blocker 2: User Not Authorized for Incus (THE BIG ONE)

**Problem:** Traditional approach requires user in `incus-admin` group. But:
- Group membership requires logout/login to take effect
- Can't add user to group at image build time (user doesn't exist)
- First-boot can add user to group, but they haven't logged out yet

**Why this is critical:** User logs in → clicks Konsole → `incus exec` fails with permission denied → broken first experience.

**Solution: Eliminate the Group Requirement**

Use a **privileged system service** that performs Incus operations on behalf of users, authorized via Polkit:

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Session                             │
│  Konsole → kapsule-enter → D-Bus call                          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (D-Bus, authorized by Polkit)
┌─────────────────────────────────────────────────────────────────┐
│              org.kde.kapsule (System Service)                   │
│                     Runs as root                                 │
│                                                                  │
│  Methods:                                                        │
│    .EnsureDefaultContainer() → creates if missing, starts it    │
│    .GetShellCommand() → returns "incus exec default -- ..."     │
│    .EnterContainer(name) → returns PTY fd or exec command       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Incus Daemon                             │
│                 (accessed by root-owned service)                 │
└─────────────────────────────────────────────────────────────────┘
```

**Polkit Policy** (`/usr/share/polkit-1/actions/org.kde.kapsule.policy`):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1.0/policyconfig.dtd">
<policyconfig>
  <action id="org.kde.kapsule.enter-container">
    <description>Enter an kapsule container</description>
    <message>Authentication is required to enter the container</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>yes</allow_active>  <!-- No password for active session! -->
    </defaults>
  </action>
  
  <action id="org.kde.kapsule.manage-container">
    <description>Create or delete kapsule containers</description>
    <defaults>
      <allow_active>auth_admin_keep</allow_active>  <!-- Password once, cached -->
    </defaults>
  </action>
</policyconfig>
```

**Result:** Active desktop users can enter containers without password. Creating/deleting requires one password prompt (cached).

---

#### Blocker 3: Incus Not Initialized

**Problem:** Fresh install has no storage pools or network configuration.

**Mitigation:** First-boot service handles this before user ever logs in.

```ini
# /usr/lib/systemd/system/kapsule-init.service
[Unit]
Description=Initialize Kapsule
ConditionPathExists=!/var/lib/kapsule/.initialized
After=incus.socket
Requires=incus.socket
Before=display-manager.service  # CRITICAL: before user can log in!

[Service]
Type=oneshot
ExecStart=/usr/lib/kapsule/init-incus.sh
ExecStartPost=/usr/bin/touch /var/lib/kapsule/.initialized
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

**Ordering guarantee:** `Before=display-manager.service` ensures init completes before SDDM shows login screen.

---

#### Blocker 4: Profiles Not Registered

**Problem:** Kapsule profiles must be in Incus's database.

**Mitigation:** Same first-boot service registers all profiles:

```bash
# /usr/lib/kapsule/init-incus.sh
#!/bin/bash
set -e

# Initialize Incus (idempotent)
incus admin init --minimal --auto 2>/dev/null || true

# Register profiles
for profile in /usr/share/kapsule/profiles/*.yaml; do
    name=$(basename "$profile" .yaml)
    incus profile show "$name" &>/dev/null || {
        incus profile create "$name"
        incus profile edit "$name" < "$profile"
    }
done

# Import bundled image
if [[ -f /usr/share/kapsule/images/arch.tar.zst ]] && \
   ! incus image show kapsule/arch &>/dev/null; then
    incus image import /usr/share/kapsule/images/arch.tar.zst --alias kapsule/arch
fi
```

---

#### Blocker 5: Container Image Not Available

**Problem:** Downloading an image takes 30-120 seconds on first run. Unacceptable for first terminal launch.

**Mitigation:** **Ship a pre-built image in the KDE Linux ISO.**

```
/usr/share/kapsule/images/arch.tar.zst  (~300MB compressed)
```

First-boot imports it to Incus. No network required for first container.

**Fallback for non-bundled distros:** Show progress in Konsole:
```
Downloading Fedora 41 image... [████████░░░░░░░░░░░░] 42%
```

---

#### Blocker 6: Default Container Doesn't Exist

**Problem:** Container must be created before user can enter it.

**Two strategies:**

**Strategy A: Pre-create at first boot (recommended)**
- Add to `init-incus.sh`: create a default container for the first user
- Challenge: We don't know the username yet at first boot

**Strategy B: Create on first Konsole launch**
- System service creates container on-demand
- Fast if using ZFS/btrfs (instant clone from image)
- 2-5 second delay on first launch

**Hybrid approach:**
```bash
# In init-incus.sh - create a template container
incus init kapsule/arch kapsule-template \
    --profile default \
    --profile kapsule-base

# First user login triggers clone + customization
# (handled by org.kde.kapsule service)
```

**On first Konsole launch:**
```python
# Pseudocode in org.kde.kapsule service
def ensure_user_container(user):
    container_name = f"kapsule-{user}"
    if not container_exists(container_name):
        # Instant clone from template (ZFS/btrfs) or copy (slower)
        incus_copy("kapsule-template", container_name)
        configure_user_mapping(container_name, user)
    if not container_running(container_name):
        incus_start(container_name)
    return container_name
```

---

#### Blocker 7: Container Startup Time

**Problem:** Booting systemd inside container takes 2-5 seconds.

**Mitigations:**

1. **Keep container running persistently**
   ```ini
   # Container configured with:
   boot.autostart: "true"
   boot.autostart.priority: 0
   ```

2. **Socket-activated container start**
   - Container starts when Konsole tries to connect
   - Show brief "Starting container..." message

3. **Warm container pool**
   - Keep template container in "frozen" state (cgroup freezer)
   - Clone from frozen state → instant resume
   - More complex but <100ms startup

**Recommended:** Keep default container running. It uses minimal resources when idle (~10MB RAM for sleeping systemd).

---

#### Blocker 8: User Identity Inside Container

**Problem:** User inside container must match host user:
- Same username
- Same UID/GID (for home directory access)
- Same home directory path

**Mitigation:** Configure idmap and bind mounts per-user:

```yaml
# Dynamic profile created per user
config:
  raw.idmap: |
    uid 1000 1000
    gid 1000 1000
  user.user: "fernie"
  
devices:
  home:
    type: disk
    source: /home/fernie
    path: /home/fernie
```

**On first enter, run setup inside container:**
```bash
# Create user inside container matching host user
useradd -u 1000 -g 1000 -d /home/fernie -s /bin/zsh fernie
```

---

### The Complete First-Run Timeline

```
┌─────────────────────────────────────────────────────────────────┐
│                     FIRST BOOT (before login)                    │
│                        ~30-60 seconds                            │
├─────────────────────────────────────────────────────────────────┤
│ 1. incus.socket activated                                        │
│ 2. kapsule-init.service runs:                                   │
│    - incus admin init                                            │
│    - Import bundled image                                        │
│    - Register profiles                                           │
│    - Create template container                                   │
│ 3. display-manager.service starts (login screen appears)         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     USER LOGS IN                                 │
├─────────────────────────────────────────────────────────────────┤
│ - Plasma session starts                                          │
│ - org.kde.kapsule user service activates (D-Bus)                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                FIRST KONSOLE LAUNCH (~3 seconds)                 │
├─────────────────────────────────────────────────────────────────┤
│ 1. Konsole calls org.kde.kapsule.EnsureDefaultContainer()       │
│ 2. Service clones template → kapsule-fernie (instant w/ ZFS)    │
│ 3. Service configures user mapping for fernie                    │
│ 4. Service starts container (systemd boot, ~2-3s)                │
│ 5. Service creates user inside container                         │
│ 6. Konsole runs: incus exec kapsule-fernie -- sudo -u fernie -i │
│ 7. User sees shell prompt                                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│               SUBSEQUENT KONSOLE LAUNCHES (<500ms)               │
├─────────────────────────────────────────────────────────────────┤
│ Container already running → instant shell                        │
└─────────────────────────────────────────────────────────────────┘
```

---

### Failure Handling: Never Leave User Without a Shell

**Critical principle:** If kapsule fails, Konsole MUST fall back to host shell.

```python
# Konsole profile logic (pseudocode)
def get_shell_command():
    try:
        container = kapsule_service.EnsureDefaultContainer(timeout=10)
        return f"incus exec {container} -- sudo -u {USER} -i"
    except KapsuleError as e:
        notify_user(f"Container unavailable: {e}. Opening host shell.")
        return "/bin/zsh"  # Fallback to host
    except TimeoutError:
        notify_user("Container taking too long to start. Opening host shell.")
        return "/bin/zsh"
```

**Visual indicator in shell prompt:**
```bash
# Inside container - show container name
PS1="📦 kapsule-fernie $ "

# Host shell (fallback) - clear indication
PS1="🖥️ kde-linux $ "
```

**Konsole tab title:**
- Container: "kapsule-fernie: ~"
- Host (fallback): "⚠️ KDE Linux (host): ~"

---

### Edge Cases to Handle

| Scenario | Handling |
|----------|----------|
| **Incus daemon crashes mid-session** | Terminal keeps working (existing exec survives). New terminals fall back to host. |
| **Container OOMs / crashes** | Konsole shows error, offers "Restart container" or "Open host shell" |
| **Disk full - can't create container** | Fall back to host, show notification with "Free up space" action |
| **User deleted container accidentally** | Service recreates on next Konsole launch |
| **Multiple users on same machine** | Each user gets their own container (kapsule-alice, kapsule-bob) |
| **System update replaces base image** | Existing containers keep running, new containers use new image |
| **Offline first boot** | Works - bundled image, no network needed |
| **Corporate proxy blocks image download** | Only affects non-default distros, default works offline |

---

### Required Components Summary

**Ship in KDE Linux Image:**
```
/usr/bin/incus
/usr/bin/kapsule
/usr/lib/kapsule/init-incus.sh
/usr/lib/systemd/system/incus.socket
/usr/lib/systemd/system/kapsule-init.service
/usr/share/dbus-1/system-services/org.kde.kapsule.service
/usr/share/polkit-1/actions/org.kde.kapsule.policy
/usr/share/kapsule/images/arch.tar.zst           # ~300MB
/usr/share/kapsule/profiles/*.yaml
/usr/share/konsole/Kapsule.profile               # Konsole profile using kapsule
```

**Runtime state:**
```
/var/lib/incus/                     # Incus database, images, containers
/var/lib/kapsule/.initialized      # First-boot marker
~/.config/kapsule/                 # User preferences
```

---

### Open Questions

1. **Storage backend:** ZFS enables instant clones but requires ZFS on host. btrfs also works. ext4 falls back to full copy (~5-10s for 1GB container). Should KDE Linux mandate ZFS/btrfs?

2. **Container persistence:** Should user changes inside container persist across reboots? (Default: yes, container has its own writable rootfs)

3. **Multiple containers:** Should Konsole have a dropdown to select container, or always use default? (Recommend: default, with right-click menu for others)

4. **System Settings integration:** Should there be a KCM to "reset container to defaults" / "switch default distro"?

5. **Updates:** How do container base images get updated? Pull new image and rebuild container? Pacman -Syu inside container?

## References

- [Incus documentation](https://linuxcontainers.org/incus/docs/main/)
- [Incus images](https://images.linuxcontainers.org/)
- [Distrobox source](https://github.com/89luca89/distrobox)
- [Arch Wiki: Incus](https://wiki.archlinux.org/title/Incus)
