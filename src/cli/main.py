#!/usr/bin/env python3
"""
Kapsule CLI - Main entry point.

Usage:
    kapsule [OPTIONS] COMMAND [ARGS]...

A distrobox-like tool using Incus as the container/VM backend,
with native KDE/Plasma integration.
"""

import os
import subprocess
from typing import Optional

import typer
from rich.table import Table

from . import __version__
from .async_typer import AsyncTyper
from .config import KapsuleConfig, get_config_path, get_config_paths, load_config, save_config
from .decorators import require_incus
from .incus_client import IncusClient, IncusError, get_client
from .models_generated import InstanceSource, InstancesPost
from .output import out
from .profile import KAPSULE_BASE_PROFILE, KAPSULE_PROFILE_NAME


# D-Bus socket path template using %t (systemd specifier for XDG_RUNTIME_DIR)
# In container drop-in: /.kapsule/host%t/kapsule/{container}/dbus.socket
# Which expands to: /.kapsule/host/run/user/{uid}/kapsule/{container}/dbus.socket
# Host sees: /run/user/{uid}/kapsule/{container}/dbus.socket
KAPSULE_DBUS_SOCKET_USER_PATH = "kapsule/{container}/dbus.socket"
KAPSULE_DBUS_SOCKET_SYSTEMD = "/.kapsule/host%t/" + KAPSULE_DBUS_SOCKET_USER_PATH
KAPSULE_DBUS_SOCKET_HOST = "/run/user/{uid}/" + KAPSULE_DBUS_SOCKET_USER_PATH


async def _setup_container_dbus_socket(
    client: IncusClient,
    container_name: str,
    uid: int,
) -> None:
    """Set up container's D-Bus session socket on a shared path.

    Creates a systemd user drop-in for dbus.socket that redirects the
    ListenStream to a path inside /.kapsule/host, making the container's
    D-Bus session socket accessible from the host.

    Args:
        client: Incus client.
        container_name: Name of the container.
        uid: User ID for the socket path.
    """
    # Build the socket paths
    # Use %t in systemd config (expands to XDG_RUNTIME_DIR inside container)
    systemd_socket_path = KAPSULE_DBUS_SOCKET_SYSTEMD.format(container=container_name)
    host_socket_path = KAPSULE_DBUS_SOCKET_HOST.format(uid=uid, container=container_name)

    # Create the directory on host (will be visible in container via mount)
    host_socket_dir = os.path.dirname(host_socket_path)
    os.makedirs(host_socket_dir, exist_ok=True)

    # Create systemd user drop-in directory
    dropin_dir = "/etc/systemd/user/dbus.socket.d"
    try:
        await client.mkdir(container_name, dropin_dir, uid=0, gid=0, mode="0755")
    except IncusError:
        pass  # Directory might already exist

    # Create the drop-in file
    # Use %t so systemd expands it to the correct user's runtime dir
    dropin_content = f"""[Socket]
# Kapsule: redirect D-Bus session socket to shared path
# This makes the container's D-Bus accessible from the host
# %t expands to XDG_RUNTIME_DIR (/run/user/UID)
ListenStream=
ListenStream={systemd_socket_path}
"""

    dropin_file = f"{dropin_dir}/kapsule.conf"
    out.info(f"Configuring container D-Bus socket at: {host_socket_path}")
    await client.push_file(
        container_name,
        dropin_file,
        dropin_content,
        uid=0,
        gid=0,
        mode="0644",
    )


async def _setup_dbus_mux_service(
    client: IncusClient,
    container_name: str,
) -> None:
    """Set up D-Bus multiplexer service for intelligent bus routing.

    Creates a systemd user service that runs kapsule-dbus-mux to intelligently
    route D-Bus calls between the host and container session buses. The service
    runs after dbus.service so the container bus is available.

    The multiplexer listens on a socket at /run/user/UID/bus (the standard
    D-Bus session bus path) and routes calls to either:
    - The container's internal D-Bus (for container-local services)
    - The host's D-Bus session bus (for desktop integration)

    Args:
        client: Incus client.
        container_name: Name of the container.
    """
    # Create systemd user service directory
    service_dir = "/etc/systemd/user"
    try:
        await client.mkdir(container_name, service_dir, uid=0, gid=0, mode="0755")
    except IncusError:
        pass  # Directory might already exist

    # The container's internal D-Bus socket (from the dbus.socket drop-in)
    # Uses %t which systemd expands to /run/user/UID
    container_dbus_socket = KAPSULE_DBUS_SOCKET_SYSTEMD.format(container=container_name)
    # Host D-Bus socket (via hostfs mount)
    host_dbus_socket = "unix:path=/.kapsule/host%t/bus"
    # Where the mux listens (standard D-Bus session bus path)
    mux_listen_socket = "%t/bus"

    # Create the kapsule-dbus-mux.service file
    service_content = f"""[Unit]
Description=Kapsule D-Bus Multiplexer
Documentation=man:kapsule(1)
After=dbus.service
Requires=dbus.service

[Service]
Type=simple
ExecStart={KAPSULE_DBUS_MUX_BIN} \\
    --listen {mux_listen_socket} \\
    --container-bus unix:path={container_dbus_socket} \\
    --host-bus {host_dbus_socket}
Restart=on-failure
RestartSec=1

[Install]
WantedBy=default.target
"""

    service_file = f"{service_dir}/kapsule-dbus-mux.service"
    out.info("Installing kapsule-dbus-mux.service for D-Bus multiplexing")
    await client.push_file(
        container_name,
        service_file,
        service_content,
        uid=0,
        gid=0,
        mode="0644",
    )

    # Enable the service globally (for all users)
    out.info("Enabling kapsule-dbus-mux.service globally")
    subprocess.run(
        ["incus", "exec", container_name, "--", "systemctl", "--user", "--global", "enable", "kapsule-dbus-mux.service"],
        capture_output=True,
    )


# Create the main Typer app
app = AsyncTyper(
    name="kapsule",
    help="Incus-based container management with KDE integration",
    add_completion=True,
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        out.info(f"kapsule version {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """
    Kapsule - Incus-based Distrobox Alternative.

    Create and manage containers that can run docker/podman inside them
    with tight KDE/Plasma integration.
    """
    pass


# Config key for session mode
KAPSULE_SESSION_MODE_KEY = "user.kapsule.session-mode"

# Config key for dbus mux mode
KAPSULE_DBUS_MUX_KEY = "user.kapsule.dbus-mux"

# Path to kapsule-dbus-mux binary inside container (via hostfs mount)
KAPSULE_DBUS_MUX_BIN = "/.kapsule/host/usr/lib/kapsule/kapsule-dbus-mux"


async def _create_container(name: str, image: str, *, session_mode: bool = False, dbus_mux: bool = False) -> None:
    """Create and start a container (internal implementation).

    Args:
        name: Name for the container.
        image: Image to use (e.g., 'images:ubuntu/24.04').
        session_mode: If True, use systemd-run for proper user sessions on enter.
        dbus_mux: If True, set up D-Bus multiplexer for intelligent host/container routing.
    """
    # dbus_mux implies session_mode (container needs its own D-Bus session)
    if dbus_mux:
        session_mode = True

    client = get_client()

    # Ensure the kapsule profile exists
    with out.operation(f"Ensuring profile: {KAPSULE_PROFILE_NAME}"):
        created = await client.ensure_profile(KAPSULE_PROFILE_NAME, KAPSULE_BASE_PROFILE)
        if created:
            out.success(f"Created profile '{KAPSULE_PROFILE_NAME}'")
        else:
            out.dim(f"Profile '{KAPSULE_PROFILE_NAME}' already exists")

    # Parse image source
    # Format: [remote:]image  e.g., "images:ubuntu/24.04" or "ubuntu/24.04"
    if ":" in image:
        server_alias, image_alias = image.split(":", 1)
        # Map common server aliases to URLs
        server_map = {
            "images": "https://images.linuxcontainers.org",
            "ubuntu": "https://cloud-images.ubuntu.com/releases",
        }
        server_url = server_map.get(server_alias)
        if not server_url:
            out.error(f"Unknown image server: {server_alias}")
            out.hint("Use 'images:' or 'ubuntu:' prefix.")
            raise typer.Exit(1)
    else:
        # Default to linuxcontainers.org
        server_url = "https://images.linuxcontainers.org"
        image_alias = image

    # Create the container
    with out.operation(f"Creating container: {name}", color="green"):
        instance_source = InstanceSource(
            type="image",
            protocol="simplestreams",
            server=server_url,
            alias=image_alias,
            allow_inconsistent=None,
            certificate=None,
            fingerprint=None,
            instance_only=None,
            live=None,
            mode=None,
            operation=None,
            project=None,
            properties=None,
            refresh=None,
            refresh_exclude_older=None,
            secret=None,
            secrets=None,
            source=None,
            **{"base-image": None},
        )
        # Build instance config with kapsule metadata
        instance_metadata: dict[str, str] = {}
        if session_mode:
            instance_metadata[KAPSULE_SESSION_MODE_KEY] = "true"
        if dbus_mux:
            instance_metadata[KAPSULE_DBUS_MUX_KEY] = "true"

        instance_config = InstancesPost(
            name=name,
            profiles=[KAPSULE_PROFILE_NAME],
            source=instance_source,
            start=True,
            architecture=None,
            config=instance_metadata if instance_metadata else None,
            description=None,
            devices=None,
            ephemeral=None,
            instance_type=None,
            restore=None,
            stateful=None,
            type=None,
        )

        out.info(f"Image: {image}")
        out.info("Downloading image and creating container...")
        operation = await client.create_instance(instance_config, wait=True)

        if operation.status != "Success":
            out.failure(f"Creation failed: {operation.err or operation.status}")
            raise typer.Exit(1)

        # In session mode, add the D-Bus socket drop-in right after creation
        # This only needs to happen once per container (not per-user)
        if session_mode:
            # Use uid 1000 as placeholder - the drop-in uses %t so it works for any user
            await _setup_container_dbus_socket(client, name, uid=1000)

            # Set up D-Bus multiplexer if requested
            if dbus_mux:
                await _setup_dbus_mux_service(client, name)

            # Reload systemd to pick up the new drop-in and services
            out.info("Reloading systemd user configuration...")
            subprocess.run(
                ["incus", "exec", name, "--", "systemctl", "--user", "--global", "daemon-reload"],
                capture_output=True,
            )

        out.success(f"Container '{name}' created successfully")


@app.command()
@require_incus
async def create(
    name: str = typer.Argument(..., help="Name of the container to create"),
    image: Optional[str] = typer.Option(
        None,
        "--image",
        "-i",
        help="Base image to use for the container (e.g., images:ubuntu/24.04)",
    ),
    session: bool = typer.Option(
        False,
        "--session",
        "-s",
        help="Enable session mode: use systemd-run for proper user sessions with container D-Bus",
    ),
    dbus_mux: bool = typer.Option(
        False,
        "--dbus-mux",
        "-m",
        help="Enable D-Bus multiplexer: intelligently route D-Bus calls between host and container buses (implies --session)",
    ),
) -> None:
    """Create a new kapsule container.

    By default, containers share the host's D-Bus session and runtime directory,
    allowing seamless integration with the host desktop environment.

    With --session, containers get their own user session via systemd-run,
    with a separate D-Bus session bus. This is useful for isolated environments
    or when you need container-local user services.

    With --dbus-mux, a D-Bus multiplexer service is set up that intelligently
    routes D-Bus calls between the host and container session buses. This allows
    applications to transparently access both host desktop services (notifications,
    file dialogs, etc.) and container-local services. Implies --session.
    """
    # Use default image from config if not specified
    if image is None:
        config = load_config()
        image = config.default_image

    client = get_client()

    # Check if container already exists
    if await client.instance_exists(name):
        out.error(f"Container '{name}' already exists.")
        raise typer.Exit(1)

    await _create_container(name, image, session_mode=session, dbus_mux=dbus_mux)


# Environment variables to skip when passing through to container
_ENTER_ENV_SKIP = frozenset({
    "_",              # Last command (set by shell)
    "SHLVL",          # Shell nesting level
    "OLDPWD",         # Previous directory
    "PWD",            # Current directory (will be wrong in container)
    "HOSTNAME",       # Host's hostname
    "HOST",           # Host's hostname (zsh)
    "LS_COLORS",      # Often huge and causes issues
    "LESS_TERMCAP_mb", "LESS_TERMCAP_md", "LESS_TERMCAP_me",  # Less colors
    "LESS_TERMCAP_se", "LESS_TERMCAP_so", "LESS_TERMCAP_ue", "LESS_TERMCAP_us",
})


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
@require_incus
async def enter(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(
        None, help="Name of the container to enter (default: from config)"
    ),
) -> None:
    """Enter a kapsule container.

    If no container name is specified, enters the default container
    (configured in ~/.config/kapsule/kapsule.conf). If the default
    container doesn't exist, it will be created automatically.

    Optionally pass a command to run instead of an interactive shell:

        kapsule enter mycontainer -- ls -la
        kapsule enter -- ls -la  # uses default container
    """
    # Load config for defaults
    config = load_config()

    # Use default container name if not specified
    if name is None:
        name = config.default_container
        out.info(f"Using default container: {name}")

    # Get user info
    uid = os.getuid()
    gid = os.getgid()
    username = os.environ.get("USER") or os.environ.get("LOGNAME") or "root"
    home_dir = os.environ.get("HOME") or f"/home/{username}"

    client = get_client()

    # Get instance - create if it doesn't exist and using default
    try:
        instance = await client.get_instance(name)
    except IncusError:
        # Container doesn't exist - create it if using default
        if name == config.default_container:
            out.warning(f"Default container '{name}' does not exist, creating it...")
            await _create_container(name, config.default_image)
            instance = await client.get_instance(name)
        else:
            out.error(f"Container '{name}' does not exist.")
            raise typer.Exit(1)

    if instance.status != "Running":
        out.error(
            f"Container '{name}' is not running "
            f"(status: {instance.status})."
        )
        out.hint(f"Start it with: incus start {name}")
        raise typer.Exit(1)

    # Check if user is already mapped in this container
    instance_config = instance.config or {}
    user_mapped_key = f"user.kapsule.host-users.{uid}.mapped"

    if instance_config.get(user_mapped_key) != "true":
        with out.operation(f"Setting up user '{username}' in container..."):
            # Add disk device to mount host home directory into container
            home_basename = os.path.basename(home_dir)
            container_home = f"/home/{home_basename}"
            device_name = f"kapsule-home-{username}"

            out.info(f"Mounting home directory: {home_dir} -> {container_home}")
            await client.add_instance_device(
                name,
                device_name,
                {
                    "type": "disk",
                    "source": home_dir,
                    "path": container_home,
                },
            )

            # Create group (allow duplicate GID with -o)
            out.info(f"Creating group '{username}' (gid={gid})")
            result = subprocess.run(
                ["incus", "exec", name, "--", "groupadd", "-o", "-g", str(gid), username],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 and "already exists" not in result.stderr:
                out.warning(f"groupadd: {result.stderr.strip()}")

            # Create user (without home directory since we symlinked it, allow duplicate UID with -o)
            out.info(f"Creating user '{username}' (uid={uid})")
            result = subprocess.run(
                [
                    "incus", "exec", name, "--",
                    "useradd",
                    "-o",           # Allow duplicate UID
                    "-M",           # Don't create home directory
                    "-u", str(uid),
                    "-g", str(gid),
                    "-d", container_home,
                    "-s", "/bin/bash",
                    username,
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 and "already exists" not in result.stderr:
                out.warning(f"useradd: {result.stderr.strip()}")

            # Add sudoers entry for passwordless sudo
            out.info(f"Configuring passwordless sudo for '{username}'")
            sudoers_content = f"{username} ALL=(ALL) NOPASSWD:ALL\n"
            sudoers_file = f"/etc/sudoers.d/{username}"
            await client.push_file(name, sudoers_file, sudoers_content, uid=0, gid=0, mode="0440")

            # Check if session mode is enabled for this container
            session_mode = instance_config.get(KAPSULE_SESSION_MODE_KEY) == "true"

            # In session mode, enable lingering so systemd --user starts at boot
            if session_mode:
                out.info(f"Enabling linger for '{username}' (session mode)")
                result = subprocess.run(
                    ["incus", "exec", name, "--", "loginctl", "enable-linger", username],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    out.warning(f"loginctl enable-linger: {result.stderr.strip()}")

            # Mark user as mapped in container config
            await client.patch_instance_config(name, {user_mapped_key: "true"})
            out.success(f"User '{username}' configured")

    # Check if session mode is enabled for this container
    session_mode = instance_config.get(KAPSULE_SESSION_MODE_KEY) == "true"
    dbus_mux_mode = instance_config.get(KAPSULE_DBUS_MUX_KEY) == "true"

    runtime_dir = f"/run/user/{uid}"
    host_runtime_dir = f"/.kapsule/host/run/user/{uid}"

    if session_mode:
        # Session mode: container has its own /run/user/$uid managed by systemd --user
        # Symlink individual host sockets into it for graphics/audio access
        # Sockets/paths to symlink from host runtime dir
        # Format: (name_or_env_var, is_env_var, subpath_override)
        runtime_links: list[tuple[str, bool, str | None]] = [
            # Wayland socket - get from WAYLAND_DISPLAY env var
            ("WAYLAND_DISPLAY", True, None),
            # X11 auth token
            ("XAUTHORITY", True, None),
            # PipeWire socket
            ("pipewire-0", False, None),
            # PulseAudio socket directory
            ("pulse", False, None),
        ]
        # Only symlink D-Bus if NOT using dbus-mux (mux service manages the bus socket)
        if not dbus_mux_mode:
            runtime_links.append(
                ("bus", False, KAPSULE_DBUS_SOCKET_USER_PATH.format(container=name))
            )

        for item, is_env, subpath in runtime_links:
            if is_env:
                # Get socket name from environment variable
                socket_name = os.environ.get(item)
                if not socket_name:
                    continue
            else:
                socket_name = item

            source = f"{host_runtime_dir}/{subpath if subpath else socket_name}"
            target = f"{runtime_dir}/{socket_name}"

            try:
                await client.create_symlink(name, target, source, uid=uid, gid=gid)
            except IncusError:
                pass  # Symlink might already exist
    else:
        # Non-session mode: symlink entire runtime dir to host's
        # This gives full access to host's D-Bus session and all sockets
        try:
            await client.mkdir(name, "/run/user", uid=0, gid=0, mode="0755")
        except IncusError:
            pass  # Directory might already exist

        try:
            await client.create_symlink(name, runtime_dir, host_runtime_dir, uid=uid, gid=gid)
        except IncusError:
            pass  # Symlink might already exist from previous enter

    # Build --env arguments from current environment
    env_args: list[str] = []
    for key, value in os.environ.items():
        if key in _ENTER_ENV_SKIP:
            continue
        # Skip variables with problematic characters
        if "\n" in value or "\x00" in value:
            continue
        env_args.extend(["--env", f"{key}={value}"])

    # Build the command to run inside the container
    if ctx.args:
        # User provided a command to run
        exec_cmd = ["su", "-l", "-c", " ".join(ctx.args), username]
    else:
        # No command - start interactive login shell
        exec_cmd = ["login", "-p", "-f", username]

    # Build full incus exec command
    exec_args = [
        "incus",
        "exec",
        name,
        *env_args,
        "--",
        *exec_cmd,
    ]

    # Replace current process with incus exec for proper TTY handling
    os.execvp("incus", exec_args)


@app.command()
async def init() -> None:
    """Initialize kapsule by enabling and starting incus sockets.

    This command must be run as root (sudo).
    """
    if os.geteuid() != 0:
        out.error("This command must be run as root.")
        out.hint("Run: [bold]sudo kapsule init[/bold]")
        raise typer.Exit(1)

    with out.operation("Initializing kapsule..."):
        # Reload systemd to pick up new unit files (in case sysext was just loaded)
        out.info("Reloading systemd daemon...")
        try:
            subprocess.run(
                ["systemctl", "daemon-reload"],
                check=True,
                capture_output=True,
                text=True,
            )
            with out.indent():
                out.success("systemd daemon reloaded")
        except subprocess.CalledProcessError as e:
            with out.indent():
                out.failure(f"Failed to reload systemd: {e.stderr.strip()}")
            raise typer.Exit(1)

        # Load kernel modules required for nested containers (Docker/Podman inside containers)
        out.info("Loading kernel modules for nested container support...")
        try:
            subprocess.run(
                ["systemctl", "restart", "systemd-modules-load"],
                check=True,
                capture_output=True,
                text=True,
            )
            with out.indent():
                out.success("Kernel modules loaded (iptables, overlay, br_netfilter)")
        except subprocess.CalledProcessError as e:
            with out.indent():
                out.warning(f"Failed to load kernel modules: {e.stderr.strip()}")
                out.dim("Nested containers (Docker inside kapsule) may not work until reboot")

        # Restart systemd-sysusers to ensure incus groups are created
        out.info("Running systemd-sysusers...")
        try:
            subprocess.run(
                ["systemd-sysusers"],
                check=True,
                capture_output=True,
                text=True,
            )
            with out.indent():
                out.success("systemd-sysusers completed")
        except subprocess.CalledProcessError as e:
            with out.indent():
                out.failure(f"Failed to run systemd-sysusers: {e.stderr.strip()}")
            raise typer.Exit(1)

        # List of socket/service units to enable
        units = [
            "incus.socket",
            "incus-user.socket",
        ]

        for unit in units:
            out.info(f"Enabling and starting {unit}...")
            try:
                subprocess.run(
                    ["systemctl", "enable", "--now", unit],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                with out.indent():
                    out.success(f"{unit} enabled and started")
            except subprocess.CalledProcessError as e:
                with out.indent():
                    out.failure(f"Failed to enable {unit}: {e.stderr.strip()}")
                raise typer.Exit(1)

        # Now use the Incus API for remaining configuration
        client = get_client()

        # Create storage pool (we don't use incus admin init --minimal because
        # it creates a network bridge we don't need - kapsule uses host networking)
        out.info("Creating storage pool...")
        if await client.storage_pool_exists("default"):
            with out.indent():
                out.dim("Storage pool 'default' already exists")
        else:
            # Try btrfs first (supports copy-on-write, snapshots)
            try:
                await client.create_storage_pool("default", "btrfs")
                with out.indent():
                    out.success("Storage pool 'default' created (btrfs backend)")
            except IncusError:
                # Fall back to dir driver (works everywhere)
                try:
                    await client.create_storage_pool("default", "dir")
                    with out.indent():
                        out.success("Storage pool 'default' created (dir backend)")
                except IncusError as e:
                    with out.indent():
                        out.failure(f"Failed to create storage pool: {e}")
                    raise typer.Exit(1)

        # Disable automatic image updates (saves bandwidth, we don't need it)
        out.info("Configuring Incus settings...")
        try:
            await client.set_server_config("images.auto_update_interval", "0")
            with out.indent():
                out.success("Disabled automatic image updates")
        except IncusError as e:
            with out.indent():
                out.warning(f"Failed to set config: {e}")

        # Add root device to default profile (for compatibility with other tools)
        out.info("Configuring default profile...")
        try:
            profile = await client.get_profile("default")
            if profile.devices and "root" in profile.devices:
                with out.indent():
                    out.dim("Default profile already configured")
            else:
                await client.add_profile_device(
                    "default",
                    "root",
                    {"type": "disk", "path": "/", "pool": "default"},
                )
                with out.indent():
                    out.success("Added root device to default profile")
        except IncusError as e:
            with out.indent():
                out.warning(f"Failed to configure default profile: {e}")

    out.section("✓ Kapsule initialized successfully!", color="green")
    out.dim("You can now use kapsule commands as a regular user.")


@app.command(name="list")
@require_incus
async def list_containers(
    all_containers: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Show all containers including stopped ones",
    ),
) -> None:
    """List kapsule containers."""
    client = get_client()
    containers = await client.list_containers()

    if not containers:
        out.dim("No containers found.")
        return

    # Filter stopped containers if --all not specified
    if not all_containers:
        containers = [c for c in containers if c.status.lower() == "running"]
        if not containers:
            out.dim("No running containers. Use --all to see stopped containers.")
            return

    # Build table
    table = Table(title="Kapsule Containers")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Status", style="green")
    table.add_column("Image", style="yellow")
    table.add_column("Created", style="dim")

    for c in containers:
        status_style = "green" if c.status.lower() == "running" else "red"
        table.add_row(
            c.name,
            f"[{status_style}]{c.status}[/{status_style}]",
            c.image,
            c.created[:10] if c.created else "",  # Just the date part
        )

    out.console.print(table)


@app.command()
@require_incus
async def rm(
    name: str = typer.Argument(..., help="Name of the container to remove"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force removal even if container is running",
    ),
) -> None:
    """Remove a kapsule container."""
    client = get_client()

    # Check if container exists
    if not await client.instance_exists(name):
        out.error(f"Container '{name}' does not exist.")
        raise typer.Exit(1)

    # Get container status
    instance = await client.get_instance(name)
    is_running = instance.status and instance.status.lower() == "running"

    # If running and force not specified, error out
    if is_running and not force:
        out.error(
            f"Container '{name}' is running. "
            "Use --force to remove it anyway."
        )
        raise typer.Exit(1)

    # If running and force specified, stop first
    if is_running and force:
        with out.operation(f"Stopping container: {name}", color="yellow"):
            stop_op = await client.stop_instance(name, force=True, wait=True)
            if stop_op.status != "Success":
                out.failure(f"Failed to stop: {stop_op.err or stop_op.status}")
                raise typer.Exit(1)
            out.success("Container stopped")

    # Delete the container
    with out.operation(f"Removing container: {name}", color="red"):
        operation = await client.delete_instance(name, wait=True)

        if operation.status != "Success":
            out.failure(f"Removal failed: {operation.err or operation.status}")
            raise typer.Exit(1)

        out.success(f"Container '{name}' removed successfully")


@app.command()
@require_incus
async def start(
    name: str = typer.Argument(..., help="Name of the container to start"),
) -> None:
    """Start a stopped kapsule container."""
    client = get_client()

    # Check if container exists
    if not await client.instance_exists(name):
        out.error(f"Container '{name}' does not exist.")
        raise typer.Exit(1)

    # Check current status
    instance = await client.get_instance(name)
    if instance.status and instance.status.lower() == "running":
        out.styled(f"[yellow]Container '{name}' is already running.[/yellow]")
        return

    with out.operation(f"Starting container: {name}", color="green"):
        operation = await client.start_instance(name, wait=True)

        if operation.status != "Success":
            out.failure(f"Start failed: {operation.err or operation.status}")
            raise typer.Exit(1)

        out.success(f"Container '{name}' started successfully")


@app.command()
@require_incus
async def stop(
    name: str = typer.Argument(..., help="Name of the container to stop"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force stop the container",
    ),
) -> None:
    """Stop a running kapsule container."""
    client = get_client()

    # Check if container exists
    if not await client.instance_exists(name):
        out.error(f"Container '{name}' does not exist.")
        raise typer.Exit(1)

    # Check current status
    instance = await client.get_instance(name)
    if instance.status and instance.status.lower() != "running":
        out.styled(f"[yellow]Container '{name}' is not running.[/yellow]")
        return

    with out.operation(f"Stopping container: {name}", color="yellow"):
        operation = await client.stop_instance(name, force=force, wait=True)

        if operation.status != "Success":
            out.failure(f"Stop failed: {operation.err or operation.status}")
            raise typer.Exit(1)

        out.success(f"Container '{name}' stopped successfully")


@app.command(name="config")
def config_cmd(
    key: Optional[str] = typer.Argument(
        None, help="Config key to get/set (default_container, default_image)"
    ),
    value: Optional[str] = typer.Argument(None, help="Value to set (omit to get)"),
) -> None:
    """View or modify kapsule configuration.

    Configuration is read from (highest to lowest priority):
      1. ~/.config/kapsule/kapsule.conf  (user)
      2. /etc/kapsule/kapsule.conf       (system)
      3. /usr/lib/kapsule/kapsule.conf   (package defaults)

    User config is written to ~/.config/kapsule/kapsule.conf

    Examples:
        kapsule config                    # Show all config
        kapsule config default_container  # Get default_container value
        kapsule config default_container mybox  # Set default_container
        kapsule config default_image images:fedora/40  # Set default_image
    """
    config = load_config()
    config_path = get_config_path()

    if key is None:
        # Show all config paths
        out.info("Config paths (highest priority first):")
        for path in get_config_paths():
            exists = "✓" if path.exists() else "✗"
            out.info(f"  {exists} {path}")
        out.info("")
        out.info(f"User config (for writes): {config_path}")
        out.info("")
        table = Table(show_header=True)
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("default_container", config.default_container)
        table.add_row("default_image", config.default_image)
        out.console.print(table)
        return

    # Validate key
    valid_keys = ["default_container", "default_image"]
    if key not in valid_keys:
        out.error(f"Unknown config key: {key}")
        out.hint(f"Valid keys: {', '.join(valid_keys)}")
        raise typer.Exit(1)

    if value is None:
        # Get value
        current_value = getattr(config, key)
        out.info(f"{key} = {current_value}")
    else:
        # Set value
        new_config = KapsuleConfig(
            default_container=(
                value if key == "default_container" else config.default_container
            ),
            default_image=value if key == "default_image" else config.default_image,
        )
        save_config(new_config)
        out.success(f"Set {key} = {value}")


def cli() -> None:
    """CLI entry point for setuptools/meson."""
    # Use KAPSULE_PROG_NAME if set (from wrapper script), otherwise default to "kapsule"
    prog_name = os.environ.get("KAPSULE_PROG_NAME", "kapsule")
    app(prog_name=prog_name)


if __name__ == "__main__":
    cli()
