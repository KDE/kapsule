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
from rich.console import Console
from rich.table import Table

from . import __version__
from .async_typer import AsyncTyper
from .incus_client import IncusClient, IncusError
from .models_generated import InstanceSource, InstancesPost
from .profile import KAPSULE_BASE_PROFILE, KAPSULE_PROFILE_NAME

# Create the main Typer app
app = AsyncTyper(
    name="kapsule",
    help="Incus-based container management with KDE integration",
    add_completion=True,
    no_args_is_help=True,
)

console = Console()


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f"kapsule version {__version__}")
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
        # Check if Incus is available
        if not await client.is_available():
            console.print("[red]Error:[/red] Cannot connect to Incus.")
            console.print(
                "[yellow]Hint:[/yellow] Run [bold]sudo kapsule init[/bold] first."
            )
            raise typer.Exit(1)

        # Check if container already exists
        if await client.instance_exists(name):
            console.print(f"[red]Error:[/red] Container '{name}' already exists.")
            raise typer.Exit(1)

        # Ensure the kapsule profile exists
        console.print(f"[bold blue]Ensuring profile:[/bold blue] {KAPSULE_PROFILE_NAME}")
        created = await client.ensure_profile(KAPSULE_PROFILE_NAME, KAPSULE_BASE_PROFILE)
        if created:
            console.print(f"  [green]✓[/green] Created profile '{KAPSULE_PROFILE_NAME}'")
        else:
            console.print(
                f"  [dim]Profile '{KAPSULE_PROFILE_NAME}' already exists[/dim]"
            )

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
                console.print(f"[red]Error:[/red] Unknown image server: {server_alias}")
                console.print(
                    "[yellow]Hint:[/yellow] Use 'images:' or 'ubuntu:' prefix."
                )
                raise typer.Exit(1)
        else:
            # Default to linuxcontainers.org
            server_url = "https://images.linuxcontainers.org"
            image_alias = image

        # Create the container
        console.print(f"[bold green]Creating container:[/bold green] {name}")
        console.print(f"  Image: {image}")

        instance_config = InstancesPost(
            name=name,
            profiles=[KAPSULE_PROFILE_NAME],
            source=InstanceSource(
                type="image",
                protocol="simplestreams",
                server=server_url,
                alias=image_alias,
            ),
            start=True,
        )

        console.print("  Downloading image and creating container...")
        operation = await client.create_instance(instance_config, wait=True)

        if not operation.succeeded:
            console.print(f"  [red]✗[/red] Creation failed: {operation.err or operation.status}")
            raise typer.Exit(1)

        console.print(f"  [green]✓[/green] Container '{name}' created successfully")

    except IncusError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
    finally:
        await client.close()


@app.command()
def enter(
    name: str = typer.Argument(..., help="Name of the container to enter"),
    command: Optional[str] = typer.Option(
        None,
        "--command",
        "-c",
        help="Command to run instead of default shell",
    ),
) -> None:
    """Enter a kapsule container."""
    console.print(f"[bold blue]Entering container:[/bold blue] {name}")
    if command:
        console.print(f"  Running: {command}")
    # TODO: Implement container entry via Incus REST API
    console.print("[yellow]⚠ Stub implementation - not yet functional[/yellow]")


@app.command()
def init() -> None:
    """Initialize kapsule by enabling and starting incus sockets.

    This command must be run as root (sudo).
    """
    if os.geteuid() != 0:
        console.print("[red]Error:[/red] This command must be run as root.")
        console.print("[yellow]Hint:[/yellow] Run: [bold]sudo kapsule init[/bold]")
        raise typer.Exit(1)

    console.print("[bold blue]Initializing kapsule...[/bold blue]")

    # Restart systemd-sysusers to ensure incus groups are created
    console.print("  Restarting systemd-sysusers...")
    try:
        subprocess.run(
            ["systemd-sysusers"],
            check=True,
            capture_output=True,
            text=True,
        )
        console.print("    [green]✓[/green] systemd-sysusers completed")
    except subprocess.CalledProcessError as e:
        console.print(f"    [red]✗[/red] Failed to run systemd-sysusers: {e.stderr.strip()}")
        raise typer.Exit(1)

    # List of socket/service units to enable
    units = [
        "incus.socket",
        "incus-user.socket",
    ]

    for unit in units:
        console.print(f"  Enabling and starting {unit}...")
        try:
            subprocess.run(
                ["systemctl", "enable", "--now", unit],
                check=True,
                capture_output=True,
                text=True,
            )
            console.print(f"    [green]✓[/green] {unit} enabled and started")
        except subprocess.CalledProcessError as e:
            console.print(f"    [red]✗[/red] Failed to enable {unit}: {e.stderr.strip()}")
            raise typer.Exit(1)

    # Initialize Incus with minimal settings (creates default storage pool)
    console.print("  Initializing Incus...")
    try:
        subprocess.run(
            ["incus", "admin", "init", "--minimal"],
            check=True,
            capture_output=True,
            text=True,
        )
        console.print("    [green]✓[/green] Incus initialized with default storage pool")
    except subprocess.CalledProcessError as e:
        # Check if already initialized
        if "already exists" in e.stderr or "already initialized" in e.stderr.lower():
            console.print("    [dim]Incus already initialized[/dim]")
        else:
            console.print(f"    [red]✗[/red] Failed to initialize Incus: {e.stderr.strip()}")
            raise typer.Exit(1)

    console.print("[bold green]✓ Kapsule initialized successfully![/bold green]")
    console.print("[dim]You can now use kapsule commands as a regular user.[/dim]")


@app.command(name="list")
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
        if not await client.is_available():
            console.print("[red]Error:[/red] Incus is not available.")
            console.print("[yellow]Hint:[/yellow] Run: [bold]sudo kapsule init[/bold] to enable incus sockets.")
            raise typer.Exit(1)

        containers = await client.list_containers()

        if not containers:
            console.print("[dim]No containers found.[/dim]")
            return

        # Filter stopped containers if --all not specified
        if not all_containers:
            containers = [c for c in containers if c.status.lower() == "running"]
            if not containers:
                console.print("[dim]No running containers. Use --all to see stopped containers.[/dim]")
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

        console.print(table)

    except IncusError as e:
        console.print(f"[red]Incus error:[/red] {e}")
        raise typer.Exit(1)
    finally:
        await client.close()


@app.command()
def rm(
    name: str = typer.Argument(..., help="Name of the container to remove"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force removal even if container is running",
    ),
) -> None:
    """Remove a kapsule container."""
    console.print(f"[bold red]Removing container:[/bold red] {name}")
    if force:
        console.print("  (forced)")
    # TODO: Implement container removal via Incus REST API
    console.print("[yellow]⚠ Stub implementation - not yet functional[/yellow]")


@app.command()
def start(
    name: str = typer.Argument(..., help="Name of the container to start"),
) -> None:
    """Start a stopped kapsule container."""
    console.print(f"[bold green]Starting container:[/bold green] {name}")
    # TODO: Implement via Incus REST API
    console.print("[yellow]⚠ Stub implementation - not yet functional[/yellow]")


@app.command()
def stop(
    name: str = typer.Argument(..., help="Name of the container to stop"),
) -> None:
    """Stop a running kapsule container."""
    console.print(f"[bold yellow]Stopping container:[/bold yellow] {name}")
    # TODO: Implement via Incus REST API
    console.print("[yellow]⚠ Stub implementation - not yet functional[/yellow]")


def cli() -> None:
    """CLI entry point for setuptools/meson."""
    # Use KAPSULE_PROG_NAME if set (from wrapper script), otherwise default to "kapsule"
    prog_name = os.environ.get("KAPSULE_PROG_NAME", "kapsule")
    app(prog_name=prog_name)


if __name__ == "__main__":
    cli()
