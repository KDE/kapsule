#!/usr/bin/env python3
"""
Kapsule CLI - Main entry point.

Usage:
    kapsule [OPTIONS] COMMAND [ARGS]...

A distrobox-like tool using Incus as the container/VM backend,
with native KDE/Plasma integration.
"""

import os
import shlex
import subprocess
from functools import wraps
from typing import Callable, Coroutine, Optional, TypeVar

import typer
from rich.table import Table

from . import __version__
from .async_typer import AsyncTyper
from .incus_client import IncusClient, IncusError
from .models_generated import InstanceSource, InstancesPost
from .output import out
from .profile import KAPSULE_BASE_PROFILE, KAPSULE_PROFILE_NAME

R = TypeVar("R")


def require_incus(func: Callable[..., Coroutine[None, None, R]]) -> Callable[..., Coroutine[None, None, R]]:
    """Decorator that checks Incus availability and handles IncusError."""
    @wraps(func)
    async def wrapper(*args: object, **kwargs: object) -> R:
        # Quick availability check with throwaway client
        client = IncusClient()
        try:
            if not await client.is_available():
                out.error("Incus is not available.")
                out.hint("Run: [bold]sudo kapsule init[/bold]")
                raise typer.Exit(1)
        finally:
            await client.close()

        # Run the actual function (with its own client)
        try:
            return await func(*args, **kwargs)
        except IncusError as e:
            out.error(str(e))
            raise typer.Exit(1)
    return wrapper


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
    image: str = typer.Option(
        "images:ubuntu/24.04",
        "--image",
        "-i",
        help="Base image to use for the container (e.g., images:ubuntu/24.04)",
    ),
) -> None:
    """Create a new kapsule container."""
    client = IncusClient()
    try:
        # Check if container already exists
        if await client.instance_exists(name):
            out.error(f"Container '{name}' already exists.")
            raise typer.Exit(1)

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
            instance_config = InstancesPost(
                name=name,
                profiles=[KAPSULE_PROFILE_NAME],
                source=instance_source,
                start=True,
                architecture=None,
                config=None,
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

            out.success(f"Container '{name}' created successfully")

    finally:
        await client.close()


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
    name: str = typer.Argument(..., help="Name of the container to enter"),
) -> None:
    """Enter a kapsule container.

    Optionally pass a command to run instead of an interactive shell:

        kapsule enter mycontainer -- ls -la
    """
    # Get user info
    uid = os.getuid()
    gid = os.getgid()
    username = os.environ.get("USER") or os.environ.get("LOGNAME") or "root"
    home_dir = os.environ.get("HOME") or f"/home/{username}"

    client = IncusClient()
    try:
        # Get instance and check it's running
        try:
            instance = await client.get_instance(name)
        except IncusError:
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
        config = instance.config or {}
        user_mapped_key = f"user.kapsule.host-users.{uid}.mapped"

        if config.get(user_mapped_key) != "true":
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

                # Mark user as mapped in container config
                await client.patch_instance_config(name, {user_mapped_key: "true"})
                out.success(f"User '{username}' configured")

        # Create symlink for XDG_RUNTIME_DIR: /run/user/{uid} -> /.kapsule/host/run/user/{uid}
        runtime_dir = f"/run/user/{uid}"
        host_runtime_dir = f"/.kapsule/host/run/user/{uid}"
        try:
            # Ensure /run/user exists
            await client.mkdir(name, "/run/user", uid=0, gid=0, mode="0755")
        except IncusError:
            pass  # Directory might already exist

        try:
            await client.create_symlink(name, runtime_dir, host_runtime_dir, uid=uid, gid=gid)
        except IncusError:
            pass  # Symlink might already exist from previous enter

    finally:
        await client.close()

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
        exec_cmd = ctx.args
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
def init() -> None:
    """Initialize kapsule by enabling and starting incus sockets.

    This command must be run as root (sudo).
    """
    if os.geteuid() != 0:
        out.error("This command must be run as root.")
        out.hint("Run: [bold]sudo kapsule init[/bold]")
        raise typer.Exit(1)

    with out.operation("Initializing kapsule..."):
        # Restart systemd-sysusers to ensure incus groups are created
        out.info("Restarting systemd-sysusers...")
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

        # Initialize Incus with minimal settings (creates default storage pool)
        out.info("Initializing Incus...")
        try:
            subprocess.run(
                ["incus", "admin", "init", "--minimal"],
                check=True,
                capture_output=True,
                text=True,
            )
            with out.indent():
                out.success("Incus initialized with default storage pool")
        except subprocess.CalledProcessError as e:
            # Check if already initialized
            if "already exists" in e.stderr or "already initialized" in e.stderr.lower():
                with out.indent():
                    out.dim("Incus already initialized")
            else:
                with out.indent():
                    out.failure(f"Failed to initialize Incus: {e.stderr.strip()}")
                raise typer.Exit(1)

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
    client = IncusClient()
    try:
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

    finally:
        await client.close()


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
    client = IncusClient()
    try:
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

    finally:
        await client.close()


@app.command()
@require_incus
async def start(
    name: str = typer.Argument(..., help="Name of the container to start"),
) -> None:
    """Start a stopped kapsule container."""
    client = IncusClient()
    try:
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

    finally:
        await client.close()


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
    client = IncusClient()
    try:
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

    finally:
        await client.close()


def cli() -> None:
    """CLI entry point for setuptools/meson."""
    # Use KAPSULE_PROG_NAME if set (from wrapper script), otherwise default to "kapsule"
    prog_name = os.environ.get("KAPSULE_PROG_NAME", "kapsule")
    app(prog_name=prog_name)


if __name__ == "__main__":
    cli()
