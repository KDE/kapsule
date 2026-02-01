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
from .daemon_client import get_daemon_client
from .decorators import require_incus
from .output import out


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
    daemon = get_daemon_client()
    success = await daemon.create_container(
        name=name,
        image=image or "",  # Empty string = daemon uses default from config
        session_mode=session,
        dbus_mux=dbus_mux,
    )

    if not success:
        raise typer.Exit(1)


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
    # Handle the case where 'name' is actually the first word of the command
    # This happens because typer's '--' handling doesn't work well with optional args
    # When user types 'kapsule enter -- cmd', typer sees 'cmd' as the name argument
    command = list(ctx.args)
    container_name = name

    # Connect to daemon
    daemon = get_daemon_client()
    await daemon.connect()

    # If a name was provided and there are extra args, check if 'name' is actually
    # a container or a command. When user types 'kapsule enter -- cmd args',
    # typer incorrectly parses 'cmd' as the container name.
    if name is not None and command:
        # Check if container exists
        try:
            await daemon.get_container_info(name)
            # Container exists, 'name' is the container
        except Exception:
            # Container doesn't exist - treat 'name' as part of the command
            command = [name] + command
            container_name = None

    # Let daemon handle everything: config, container creation/start, user setup, symlinks
    # The daemon obtains our credentials and environment from D-Bus and /proc
    success, error, exec_args = await daemon.prepare_enter(
        container_name=container_name,
        command=command,
    )

    if not success:
        out.error(error)
        raise typer.Exit(1)

    # Replace current process with incus exec for proper TTY handling
    os.execvp(exec_args[0], exec_args)


# Path to the init script installed by CMake
_KAPSULE_INIT_SCRIPT = "/usr/lib/kapsule/kapsule-init.sh"


@app.command()
def init() -> None:
    """Initialize kapsule by enabling and starting incus sockets.

    This command must be run as root (sudo).
    """
    if os.geteuid() != 0:
        out.error("This command must be run as root.")
        out.hint("Run: [bold]sudo kapsule init[/bold]")
        raise typer.Exit(1)

    # Find the init script - check installed location first, then development location
    script_paths = [
        _KAPSULE_INIT_SCRIPT,
        os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "kapsule-init.sh"),
    ]
    
    init_script = None
    for path in script_paths:
        if os.path.isfile(path):
            init_script = path
            break
    
    if not init_script:
        out.error(f"Init script not found. Looked in: {script_paths}")
        raise typer.Exit(1)

    # Execute the init script directly
    result = subprocess.run(["bash", init_script])
    raise typer.Exit(result.returncode)


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
@require_incus
async def config_cmd(
    key: Optional[str] = typer.Argument(
        None, help="Config key to display (default_container, default_image)"
    ),
) -> None:
    """View kapsule configuration.

    Configuration is read from (highest to lowest priority):
      1. ~/.config/kapsule/kapsule.conf  (user)
      2. /etc/kapsule/kapsule.conf       (system)
      3. /usr/lib/kapsule/kapsule.conf   (package defaults)

    Examples:
        kapsule config                    # Show all config
        kapsule config default_container  # Get default_container value
    """
    daemon = get_daemon_client()
    await daemon.connect()

    config = await daemon.get_config()

    if "error" in config:
        out.error(config["error"])
        raise typer.Exit(1)

    if key is None:
        # Show all config
        table = Table(show_header=True)
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("default_container", config.get("default_container", ""))
        table.add_row("default_image", config.get("default_image", ""))
        out.console.print(table)
        return

    # Validate key
    valid_keys = ["default_container", "default_image"]
    if key not in valid_keys:
        out.error(f"Unknown config key: {key}")
        out.hint(f"Valid keys: {', '.join(valid_keys)}")
        raise typer.Exit(1)

    out.info(f"{key} = {config.get(key, '')}")


def cli() -> None:
    """CLI entry point for setuptools/meson."""
    prog_name = os.environ.get("KAPSULE_PROG_NAME", "kapsule")
    app(prog_name=prog_name)


if __name__ == "__main__":
    cli()
