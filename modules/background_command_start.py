"""Non-blocking background command creation with a cancellation handshake."""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict


async def start_background_command(manager: Any, **kwargs: Any) -> Dict[str, Any]:
    cancelled = threading.Event()
    command_handle: Dict[str, str] = {}

    def created(command_id: str) -> None:
        command_handle["id"] = command_id
        if cancelled.is_set():
            manager.cancel_command(command_id)

    try:
        return await asyncio.to_thread(
            manager.create_background_command,
            _cancel_event=cancelled, _on_created=created, **kwargs,
        )
    except asyncio.CancelledError:
        cancelled.set()
        command_id = command_handle.get("id")
        if command_id:
            await asyncio.to_thread(manager.cancel_command, command_id)
        raise
