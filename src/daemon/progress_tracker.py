# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Track Incus operation progress and relay via OperationReporter.

Subscribes to the Incus operation events websocket
(``/1.0/events?type=operation``) over the Incus Unix socket. Raw
``download_progress`` text from Incus is forwarded to the UI via the
``ProgressTextUpdate`` D-Bus signal without any parsing.
"""

import asyncio
import contextlib
import json
import logging
from typing import cast

from websockets.asyncio.client import unix_connect
from websockets.exceptions import ConnectionClosed

from .incus_client import IncusClient
from .models_generated import Operation
from .operations import OperationReporter

logger = logging.getLogger(__name__)


async def _monitor_operation_progress(
    operation_id: str,
    queue: asyncio.Queue[str],
    socket_path: str,
) -> None:
    """Monitor Incus operation events via the websocket events API.

    Connects to ``ws://localhost/1.0/events?type=operation`` over the
    Incus Unix socket and forwards ``download_progress`` strings for the
    given operation to *queue*. Exits when the operation reaches a
    terminal status (``Success``, ``Failure``, or ``Cancelled``).
    """
    try:
        async with unix_connect(
            path=socket_path,
            uri="ws://localhost/1.0/events?type=operation",
            proxy=None,
            user_agent_header=None,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
        ) as websocket:
            async for message in websocket:
                text = (
                    message.decode("utf-8", errors="replace")
                    if isinstance(message, bytes)
                    else message
                )

                try:
                    event_raw = json.loads(text)
                except json.JSONDecodeError:
                    logger.debug("Failed to decode Incus event: %s", text[:200])
                    continue

                if not isinstance(event_raw, dict):
                    continue
                event = cast(dict[str, object], event_raw)

                event_metadata = event.get("metadata")
                if not isinstance(event_metadata, dict):
                    continue
                event_metadata_dict = cast(dict[str, object], event_metadata)

                if event_metadata_dict.get("id") != operation_id:
                    continue

                op_metadata = event_metadata_dict.get("metadata")
                if isinstance(op_metadata, dict):
                    op_metadata_dict = cast(dict[str, object], op_metadata)
                    raw_progress = op_metadata_dict.get("download_progress")
                    if isinstance(raw_progress, str):
                        await queue.put(raw_progress)

                status = event_metadata_dict.get("status")
                if status in ("Success", "Failure", "Cancelled"):
                    return
    except ConnectionClosed:
        logger.debug("Incus operation event websocket closed")
        return


async def wait_operation_with_progress(
    incus: IncusClient,
    operation_id: str,
    progress: OperationReporter,
    description: str,
    *,
    timeout: int = 600,
    poll_interval: float = 1.5,
) -> Operation:
    """Wait for an Incus operation, reporting download progress.

    Monitors the Incus operation events websocket
    (``/1.0/events?type=operation``) for live download progress and
    relays raw text updates through the *progress* reporter. The CLI
    renders these as an indeterminate
    spinner with the latest Incus progress text.

    Args:
        incus: Incus client instance.
        operation_id: Incus operation UUID.
        progress: Reporter to emit progress signals through.
        description: Human-readable description (e.g., "Downloading image...").
        timeout: Maximum seconds to wait for the operation to complete.
        poll_interval: How often (seconds) to re-check whether the
            operation has finished when the event stream is quiet.

    Returns:
        The completed Operation.
    """
    # Use indeterminate progress (total=-1) since we relay raw text
    bar = progress.start_progress(description, total=-1)
    last_text = ""
    tick = 0
    progress_queue: asyncio.Queue[str] = asyncio.Queue()
    wait_task = asyncio.create_task(incus.wait_operation(operation_id, timeout=timeout))
    monitor_task = asyncio.create_task(
        _monitor_operation_progress(operation_id, progress_queue, incus.socket_path)
    )

    try:
        while not wait_task.done():
            try:
                raw_progress = await asyncio.wait_for(
                    progress_queue.get(),
                    timeout=poll_interval,
                )
            except TimeoutError:
                # Tick the spinner even when no new text arrives
                tick += 1
                bar.update(tick)
                continue

            # Deduplicate: only emit when the text actually changes
            if raw_progress != last_text:
                last_text = raw_progress
                bar.update_text(raw_progress)
            tick += 1
            bar.update(tick)

        final_op = await wait_task
        if final_op.status == "Success":
            bar.complete(success=True)
        else:
            bar.complete(
                success=False,
                message=final_op.err or f"Status: {final_op.status}",
            )

        return final_op

    except Exception:
        bar.complete(success=False)
        raise
    finally:
        monitor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await monitor_task
