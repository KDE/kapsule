"""Entry point for running the daemon directly.

Usage:
    python -m kapsule.daemon
    python -m kapsule.daemon --system  # Use system bus (requires root/polkit)
"""

from __future__ import annotations

import asyncio
import argparse
import signal
import sys


async def main(bus_type: str = "session") -> None:
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
        done, pending = await asyncio.wait(
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
    parser = argparse.ArgumentParser(description="Kapsule D-Bus daemon")
    parser.add_argument(
        "--system",
        action="store_true",
        help="Use system bus instead of session bus",
    )
    args = parser.parse_args()

    bus_type = "system" if args.system else "session"

    try:
        asyncio.run(main(bus_type))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
