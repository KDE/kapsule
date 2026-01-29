#!/usr/bin/env python3
"""
Kapsule CLI - Main entry point.

Usage:
    kapsule [OPTIONS] COMMAND [ARGS]...

A distrobox-like tool using Incus as the container/VM backend,
with native KDE/Plasma integration.
"""

import asyncio
import typer
from rich.console import Console
from rich.table import Table
from typing import Optional

from kapsule import __version__
from kapsule.incus_client import IncusClient, IncusError

# Create the main Typer app
app = typer.Typer(
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
def create(
    name: str = typer.Argument(..., help="Name of the container to create"),
    image: str = typer.Option(
        "ubuntu:24.04",
        "--image",
        "-i",
        help="Base image to use for the container",
    ),
    with_docker: bool = typer.Option(
        False,
        "--with-docker",
        help="Enable Docker support inside the container",
    ),
    with_graphics: bool = typer.Option(
        True,
        "--with-graphics",
        help="Enable graphics/GPU passthrough",
    ),
    with_audio: bool = typer.Option(
        True,
        "--with-audio",
        help="Enable audio (PipeWire/PulseAudio) passthrough",
    ),
    with_home: bool = typer.Option(
        True,
        "--with-home",
        help="Mount home directory inside container",
    ),
) -> None:
    """Create a new kapsule container."""
    console.print(f"[bold green]Creating container:[/bold green] {name}")
    console.print(f"  Image: {image}")
    console.print(f"  Docker: {with_docker}")
    console.print(f"  Graphics: {with_graphics}")
    console.print(f"  Audio: {with_audio}")
    console.print(f"  Home mount: {with_home}")
    # TODO: Implement actual container creation via Incus REST API
    console.print("[yellow]⚠ Stub implementation - not yet functional[/yellow]")


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


@app.command(name="list")
def list_containers(
    all_containers: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Show all containers including stopped ones",
    ),
) -> None:
    """List kapsule containers."""
    async def _list() -> None:
        client = IncusClient()
        try:
            if not await client.is_available():
                console.print("[red]Error:[/red] Incus is not available. Is the service running?")
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

    asyncio.run(_list())


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
    app()


if __name__ == "__main__":
    cli()
