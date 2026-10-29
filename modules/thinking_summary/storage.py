"""Display-only reasoning summaries, isolated by the actual history directory."""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

from utils.conversation_manager.locking import directory_lock


def summary_path(manager, conversation_id: str) -> Path:
    # Reuse the manager's ID validation and namespace/workspace resolution.
    conversation = manager._get_conversation_file_path(conversation_id)
    return conversation.parent / "thinking_summaries" / conversation.name


def read_summaries(manager, conversation_id: str) -> dict:
    path = summary_path(manager, conversation_id)
    with directory_lock(path.parent):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Invalid thinking summary display file")
        responses = data.get("responses", {})
        if not isinstance(responses, dict) or any(
            not isinstance(entry, dict) or not isinstance(entry.get("text"), str)
            or not isinstance(entry.get("revision"), int)
            for entry in responses.values()
        ):
            raise ValueError("Invalid thinking summary entries")
        return responses


def save_summary(manager, conversation_id: str, response_id: str, entry: dict) -> None:
    path = summary_path(manager, conversation_id)
    with directory_lock(path.parent):
        responses = read_summaries(manager, conversation_id)
        responses[response_id] = deepcopy(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".summary-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "responses": responses}, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def decorate_snapshot(manager, conversation_id: str, document: dict, base: list,
                      projected: list) -> None:
    """Only decorate detached display copies; never model context or stored history."""
    try:
        summaries = read_summaries(manager, conversation_id)
    except Exception:
        from . import log_failure
        log_failure("read_display")
        return
    for message in [*(document.get("messages") or []), *base]:
        entry = summaries.get(message.get("message_id"))
        if entry:
            message["thinking_summary"] = deepcopy(entry)
    for message in projected:
        for action in message.get("actions", []):
            entry = summaries.get(action.get("responseMessageId"))
            if action.get("type") == "thinking" and entry:
                # A stale retry generation must never decorate the replacement block.
                if action.get("thinkingGeneration") != entry.get("generation"):
                    continue
                action["thinkingSummary"] = entry["text"]
                action["thinkingSummaryRevision"] = entry["revision"]
