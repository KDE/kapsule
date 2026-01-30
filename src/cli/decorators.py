"""Decorators for CLI commands."""

from functools import wraps
from typing import Callable, Coroutine, TypeVar

import typer

from .incus_client import IncusError, get_client
from .output import out

R = TypeVar("R")


def require_incus(func: Callable[..., Coroutine[None, None, R]]) -> Callable[..., Coroutine[None, None, R]]:
    """Decorator that checks Incus availability and handles IncusError."""
    @wraps(func)
    async def wrapper(*args: object, **kwargs: object) -> R:
        client = get_client()
        if not await client.is_available():
            out.error("Incus is not available.")
            out.hint("Run: [bold]sudo kapsule init[/bold]")
            raise typer.Exit(1)

        try:
            return await func(*args, **kwargs)
        except IncusError as e:
            out.error(str(e))
            raise typer.Exit(1)
    return wrapper
