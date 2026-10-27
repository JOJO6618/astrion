"""Account-scoped Chat storage using the shared conversation JSON manager."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Callable, Dict, Optional

from utils.conversation_manager import ConversationManager
from modules.chat_mode.tree import MessageTree, TreeConflict, initial_state


class ChatStorage:
    def __init__(self, root: Path, *, read_only: bool = False):
        self.root = Path(root)
        self.manager = ConversationManager(
            base_dir=str(self.root), conversation_kind="chat", read_only=read_only,
        )

    def create(self, *, model_key=None, thinking_mode=False,
               reasoning_effort=None, network_enabled=True, creation_operation_id=None) -> str:
        state = initial_state()
        state["creation_operation_id"] = creation_operation_id
        return self.manager.create_conversation(
            project_path=None, thinking_mode=thinking_mode,
            run_mode="thinking" if thinking_mode else "fast", model_key=model_key,
            metadata_overrides={
                "conversation_kind": "chat", "chat": state,
                "reasoning_effort": reasoning_effort,
                "network_enabled": bool(network_enabled), "network_revision": 0,
                "pending_network_notices": [],
            },
        )

    def create_for_operation(self, operation_id, **settings):
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
            raise ValueError("A stable send operation ID is required")
        with self.manager._io_lock:
            for path in self.manager.conversations_dir.glob("conv_*.json"):
                document = self.read(path.stem)
                if document["metadata"]["chat"].get("creation_operation_id") == operation_id:
                    return document["id"]
            return self.create(creation_operation_id=operation_id, **settings)

    def read(self, conversation_id: str) -> Dict:
        document = self.manager.read_conversation(conversation_id)
        if document is None:
            raise FileNotFoundError("Chat conversation unavailable")
        MessageTree(document)
        return document

    def project(self, conversation_id: str, leaf_id=None, *, latest=True) -> Dict:
        document = self.read(conversation_id)
        document["messages"] = MessageTree(document).path(leaf_id, latest=latest)
        return document

    def command(self, conversation_id: str, operation_id: str, payload: Dict,
                apply: Callable, *, expected_revision: Optional[int] = None) -> Dict:
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128:
            raise ValueError("A stable operation ID is required")
        fingerprint = hashlib.sha256(json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode()).hexdigest()

        def mutate(document):
            tree = MessageTree(document)
            previous = tree.state["commands"].get(operation_id)
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise TreeConflict("Operation ID reused for a different command")
                return previous["result"]
            tree.check_revision(expected_revision)
            result = apply(tree)
            tree.validate()
            receipt = {"revision": tree.state["revision"], "value": result}
            tree.state["commands"][operation_id] = {
                "fingerprint": fingerprint, "result": deepcopy(receipt),
            }
            return receipt

        _, result = self.manager.mutate_conversation(conversation_id, mutate)
        return result

    def begin(self, conversation_id, operation_id, *, parent_id=None,
              message=None, regenerate_turn_id=None, edit_message_id=None,
              expected_revision=None, presentation_id=None):
        payload = {"action": "begin", "parent_id": parent_id, "message": message,
                   "regenerate_turn_id": regenerate_turn_id,
                   "edit_message_id": edit_message_id}

        def apply(tree):
            from modules.chat_mode.handoff_records import continue_after_send
            if any(s["status"] == "preparing" for s in tree.state.get("handoffs", {}).values()):
                raise TreeConflict("Wait for the current Agent handoff to finish")
            previous_leaf = tree.state["latest_leaf_id"]
            if not tree.messages and presentation_id:
                tree.state["handoff_presentation_id"] = presentation_id
            if edit_message_id is not None:
                if regenerate_turn_id is not None or message is None:
                    raise ValueError("Invalid edit-and-send command")
                result = tree.fork_user(edit_message_id, message["content"])
            else:
                result = tree.begin_turn(parent_id=parent_id, user_message=message,
                                         regenerate_turn_id=regenerate_turn_id)
            continue_after_send(tree, previous_leaf)
            return result
        return self.command(conversation_id, operation_id, payload, apply,
                            expected_revision=expected_revision)

    def append(self, conversation_id, turn_id, message):
        # The message ID is also the retry key: reconnecting must never append
        # the same result twice or execute a second turn.
        message_id = message.get("message_id")
        if not message_id:
            raise ValueError("Persisted messages require a stable ID")
        stable_message = {k: v for k, v in message.items() if k != "timestamp"}

        def apply(tree):
            stored = tree.append_to_turn(turn_id, message)
            return {"message_id": stored["message_id"]}

        self.command(conversation_id, message_id,
                     {"action": "append", "turn": turn_id, "message": stable_message}, apply)
        return deepcopy(MessageTree(self.read(conversation_id)).node(message_id))

    def edit(self, conversation_id, operation_id, message_id, content,
             *, expected_revision=None):
        def apply(tree):
            from modules.chat_mode.handoff_records import sync
            tree.edit(message_id, content)
            sync(tree.document)
            return {"message_id": message_id}
        return self.command(
            conversation_id, operation_id,
            {"action": "edit", "message_id": message_id, "content": content}, apply,
            expected_revision=expected_revision,
        )

    def finish(self, conversation_id, turn_id, status="complete"):
        return self.command(
            conversation_id, f"finish:{turn_id}:{status}",
            {"action": "finish", "turn": turn_id, "status": status},
            lambda tree: tree.finish_turn(turn_id, status),
        )

    def set_network(self, conversation_id, operation_id, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("Network state must be a boolean")

        def apply(tree):
            metadata = tree.document["metadata"]
            if metadata["network_enabled"] != enabled:
                metadata["network_enabled"] = enabled
                metadata["network_revision"] += 1
                metadata["pending_network_notices"].append({
                    "revision": metadata["network_revision"], "enabled": enabled,
                })
                tree._touch()
            return {"enabled": enabled, "revision": metadata["network_revision"]}
        return self.command(conversation_id, operation_id,
                            {"action": "network", "enabled": enabled}, apply)

    def search(self, query: str, limit: int = 20) -> list:
        if not query.strip():
            return []
        needle = query.casefold()
        results = []
        # Indexes are expendable; the authoritative branch projection supplies
        # the text, so old versions never leak into search results.
        with self.manager._io_lock:
            paths = sorted(self.manager.conversations_dir.glob("conv_*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            for path in paths:
                document = self.read(path.stem)
                hits = []
                for message in MessageTree(document).path():
                    if message.get("metadata", {}).get("visibility") == "hidden":
                        continue
                    content = message.get("content", "")
                    text = content if isinstance(content, str) else "\n".join(
                        p.get("text", "") for p in content if isinstance(p, dict)
                    )
                    if needle in text.casefold():
                        hits.append({"message_id": message["message_id"], "text": text})
                if hits or needle in document["title"].casefold():
                    results.append({"id": document["id"], "title": document["title"],
                                    "matches": hits})
                if len(results) >= max(1, min(limit, 100)):
                    break
        return results
