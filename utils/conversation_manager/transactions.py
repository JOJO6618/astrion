"""Atomic document mutations shared by linear and branching conversations."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Callable, Dict, TypeVar

from utils.conversation_manager.locking import locked_write

T = TypeVar("T")


class ConversationTransactionsMixin:
    @locked_write
    def mutate_conversation(
        self, conversation_id: str, mutate: Callable[[Dict], T]
    ) -> tuple[Dict, T]:
        """Read, mutate and commit under the directory's shared lock.

        The callback receives a detached document. Exceptions abort the write;
        unlike linear save this operation never merges a projected history.
        Callers implement their own revision/operation checks in the callback.
        """
        if self.read_only:
            raise PermissionError("Conversation manager is read-only")
        path = self._get_conversation_file_path(conversation_id)
        # Strict reading is deliberate: corrupt data must not become an empty
        # conversation through the compatibility reader's None fallback.
        import json
        with path.open("r", encoding="utf-8") as stream:
            document = json.load(stream)
        if not isinstance(document, dict) or document.get("id") != conversation_id:
            raise ValueError("Invalid conversation document")
        before = deepcopy(document)
        result = mutate(document)
        if document == before:
            return deepcopy(document), deepcopy(result)
        document["updated_at"] = datetime.now().isoformat()
        metadata = document.setdefault("metadata", {})
        messages = document.get("messages", [])
        metadata["total_messages"] = len(messages)
        metadata["total_tools"] = self._count_tools_in_messages(messages)
        metadata["has_images"] = any(m.get("images") or m.get("image_files") for m in messages)
        metadata["has_videos"] = any(m.get("videos") or m.get("video_files") for m in messages)
        if not metadata.get("title_locked"):
            document["title"] = self._extract_title_from_messages(messages)
        self._save_conversation_file(conversation_id, document)
        self._update_index(conversation_id, document)
        return deepcopy(document), deepcopy(result)
