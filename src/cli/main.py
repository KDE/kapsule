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
from .daemon_client import get_daemon_client, DaemonClient
from .decorators import require_incus
from .incus_client import IncusClient, IncusError, get_client
from .output import out


# D-Bus socket path template for enter command
KAPSULE_DBUS_SOCKET_USER_PATH = "kapsule/{container}/dbus.socket"

# Config keys for kapsule metadata stored in container config
KAPSULE_SESSION_MODE_KEY = "user.kapsule.session-mode"
KAPSULE_DBUS_MUX_KEY = "user.kapsule.dbus-mux"


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

    daemon = get_daemon_client()
    success = await daemon.create_container(
        name=name,
        image=image,
        session_mode=session,
        dbus_mux=dbus_mux,
    )

    if not success:
        raise typer.Exit(1)


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

    daemon = get_daemon_client()
    await daemon.connect()

    # Get container info from daemon
    try:
        info = await daemon.get_container_info(name)
    except Exception:
        # Container doesn't exist - create it if using default
        if name == config.default_container:
            out.warning(f"Default container '{name}' does not exist, creating it...")
            success = await daemon.create_container(name, config.default_image)
            if not success:
                raise typer.Exit(1)
            info = await daemon.get_container_info(name)
        else:
            out.error(f"Container '{name}' does not exist.")
            raise typer.Exit(1)

    status = info.get("status", "Unknown")
    if status != "Running":
        out.error(f"Container '{name}' is not running (status: {status}).")
        out.hint(f"Start it with: kapsule start {name}")
        raise typer.Exit(1)

    # Check if user is already set up in this container
    if not await daemon.is_user_setup(name, uid):
        success = await daemon.setup_user(
            container_name=name,
            uid=uid,
            gid=gid,
            username=username,
            home_dir=home_dir,
        )
        if not success:
            raise typer.Exit(1)

    # Handle runtime directory symlinks (needs to be done on host side)
    # This part still uses direct Incus access for file operations
    client = get_client()
    instance = await client.get_instance(name)
    instance_config = instance.config or {}

    session_mode = instance_config.get(KAPSULE_SESSION_MODE_KEY) == "true"
    dbus_mux_mode = instance_config.get(KAPSULE_DBUS_MUX_KEY) == "true"

    runtime_dir = f"/run/user/{uid}"
    host_runtime_dir = f"/.kapsule/host/run/user/{uid}"

    if session_mode:
        # Session mode: container has its own /run/user/$uid managed by systemd --user
        # Symlink individual host sockets into it for graphics/audio access
        runtime_links: list[tuple[str, bool, str | None]] = [
            ("WAYLAND_DISPLAY", True, None),
            ("XAUTHORITY", True, None),
            ("pipewire-0", False, None),
            ("pulse", False, None),
        ]
        if not dbus_mux_mode:
            runtime_links.append(
                ("bus", False, KAPSULE_DBUS_SOCKET_USER_PATH.format(container=name))
            )

        for item, is_env, subpath in runtime_links:
            if is_env:
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
        try:
            await client.mkdir(name, "/run/user", uid=0, gid=0, mode="0755")
        except IncusError:
            pass

        try:
            await client.create_symlink(name, runtime_dir, host_runtime_dir, uid=uid, gid=gid)
        except IncusError:
            pass

    # Build --env arguments from current environment
    env_args: list[str] = []
    for key, value in os.environ.items():
        if key in _ENTER_ENV_SKIP:
            continue
        if "\n" in value or "\x00" in value:
            continue
        env_args.extend(["--env", f"{key}={value}"])

    # Build the command to run inside the container
    if ctx.args:
        exec_cmd = ["su", "-l", "-c", " ".join(ctx.args), username]
    else:
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
        # Reload systemd to pick up new unit files
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

        # Load kernel modules
        out.info("Loading kernel modules for nested container support...")
        try:
            subprocess.run(
                ["systemctl", "restart", "systemd-modules-load"],
                check=True,
                capture_output=True,
                text=True,
            )
            with out.indent():
                out.success("Kernel modules loaded")
        except subprocess.CalledProcessError as e:
            with out.indent():
                out.warning(f"Failed to load kernel modules: {e.stderr.strip()}")

        # Run systemd-sysusers
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

        # Enable and start incus sockets
        units = ["incus.socket", "incus-user.socket"]
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

        # Use the Incus API for storage pool and profile setup
        client = get_client()

        # Create storage pool
        out.info("Creating storage pool...")
        if await client.storage_pool_exists("default"):
            with out.indent():
                out.dim("Storage pool 'default' already exists")
        else:
            try:
                await client.create_storage_pool("default", "btrfs")
                with out.indent():
                    out.success("Storage pool 'default' created (btrfs backend)")
            except IncusError:
                try:
                    await client.create_storage_pool("default", "dir")
                    with out.indent():
                        out.success("Storage pool 'default' created (dir backend)")
                except IncusError as e:
                    with out.indent():
                        out.failure(f"Failed to create storage pool: {e}")
                    raise typer.Exit(1)

        # Disable automatic image updates
        out.info("Configuring Incus settings...")
        try:
            await client.set_server_config("images.auto_update_interval", "0")
            with out.indent():
                out.success("Disabled automatic image updates")
        except IncusError as e:
            with out.indent():
                out.warning(f"Failed to set config: {e}")

        # Configure default profile
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
    daemon = get_daemon_client()
    await daemon.connect()

    containers = await daemon.list_containers()

    if not containers:
        out.dim("No containers found.")
        return

    # Filter stopped containers if --all not specified
    if not all_containers:
        containers = [c for c in containers if c[1].lower() == "running"]
        if not containers:
            out.dim("No running containers. Use --all to see stopped containers.")
            return

    # Build table
    table = Table(title="Kapsule Containers")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Status", style="green")
    table.add_column("Image", style="yellow")
    table.add_column("Mode", style="magenta")
    table.add_column("Created", style="dim")

    for name, status, image, created, mode in containers:
        status_style = "green" if status.lower() == "running" else "red"
        table.add_row(
            name,
            f"[{status_style}]{status}[/{status_style}]",
            image,
            mode,
            created[:10] if created else "",
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
    daemon = get_daemon_client()
    success = await daemon.delete_container(name=name, force=force)

    if not success:
        raise typer.Exit(1)


@app.command()
@require_incus
async def start(
    name: str = typer.Argument(..., help="Name of the container to start"),
) -> None:
    """Start a stopped kapsule container."""
    daemon = get_daemon_client()
    success = await daemon.start_container(name=name)

    if not success:
        raise typer.Exit(1)


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
    daemon = get_daemon_client()
    success = await daemon.stop_container(name=name, force=force)

    if not success:
        raise typer.Exit(1)


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
        current_value = getattr(config, key)
        out.info(f"{key} = {current_value}")
    else:
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
    prog_name = os.environ.get("KAPSULE_PROG_NAME", "kapsule")
    app(prog_name=prog_name)


if __name__ == "__main__":
    cli()
