"""Entry point for running the daemon directly.

Usage:
    python -m kapsule.daemon
    python -m kapsule.daemon --system  # Use system bus (default, requires root/polkit)
    python -m kapsule.daemon --session # Use session bus (for testing)
"""

from __future__ import annotations

import asyncio
import argparse
import signal


async def run_daemon(bus_type: str = "system") -> None:
    """Run the Kapsule D-Bus daemon."""
    from .service import KapsuleService

    service = KapsuleService(bus_type=bus_type)

    # Handle shutdown signals
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def handle_signal() -> None:
        print("\nShutting down...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    try:
        await service.start()

        # Wait for either disconnect or shutdown signal
        _, pending = await asyncio.wait(
            [
                asyncio.create_task(service.run()),
                asyncio.create_task(shutdown_event.wait()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel pending tasks
        for task in pending:
            task.cancel()

    finally:
        await service.stop()


def run() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Kapsule D-Bus daemon for container management",
    )
    parser.add_argument(
        "--system",
        action="store_true",
        default=True,
        help="Use system bus (default)",
    )
    parser.add_argument(
        "--session",
        action="store_true",
        help="Use session bus instead of system bus (for testing)",
    )
    parser.add_argument(
        "--socket",
        default="/var/lib/incus/unix.socket",
        help="Path to Incus Unix socket",
    )

    args = parser.parse_args()

    # Determine bus type
    bus_type = "session" if args.session else "system"

    try:
        asyncio.run(run_daemon(bus_type))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
