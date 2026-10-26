"""Message-tree operations; model drivers consume only ``path`` projections.

Messages retain the ordinary conversation wire format. Only metadata.chat holds
parent/turn identity; the document owns the latest branch and turn reservations.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4


class TreeConflict(ValueError):
    """The document changed, or a command targets an unavailable generation."""


def identifier(prefix: str = "msg") -> str:
    return f"{prefix}_{uuid4().hex}"


def initial_state() -> Dict[str, Any]:
    return {"version": 1, "revision": 0, "latest_leaf_id": None,
            "active_turn_id": None, "turns": [], "commands": {}, "summaries": []}


class MessageTree:
    """Mutates a detached document inside a ConversationManager transaction."""

    def __init__(self, document: Dict[str, Any]):
        self.document = document
        if document.get("metadata", {}).get("conversation_kind") != "chat":
            raise ValueError("Not a Chat conversation")
        self.state = document["metadata"]["chat"]
        if self.state.get("version") != 1:
            raise ValueError("Unsupported Chat tree version")
        self.messages = document["messages"]
        if not isinstance(self.messages, list) or any(
            not isinstance(m, dict) or not isinstance(m.get("message_id"), str)
            or not m["message_id"] for m in self.messages
        ):
            raise ValueError("Every message needs a nonempty string identity")
        self.by_id = {m["message_id"]: m for m in self.messages}
        self.validate()

    def validate(self) -> None:
        if len(self.by_id) != len(self.messages):
            raise ValueError("Duplicate message identity")
        # Append order is topological. This also rejects cycles in linear time.
        seen = set()
        if not isinstance(self.state.get("commands"), dict):
            raise ValueError("Invalid command receipts")
        turn_ids = {t["id"] for t in self.state["turns"]}
        if len(turn_ids) != len(self.state["turns"]):
            raise ValueError("Duplicate turn identity")
        for message in self.messages:
            relation = message.get("metadata", {}).get("chat")
            if not isinstance(relation, dict) or "parent_id" not in relation:
                raise ValueError("Missing Chat message relationship")
            parent = relation["parent_id"]
            if parent is not None and parent not in seen:
                raise ValueError("Missing or non-topological parent")
            if relation.get("turn_id") not in turn_ids | {None}:
                raise ValueError("Unknown message turn")
            seen.add(message["message_id"])
        if self.state["latest_leaf_id"] is not None:
            self.node(self.state["latest_leaf_id"])
        for turn in self.state["turns"]:
            if self.node(turn["parent_id"])["role"] != "user":
                raise ValueError("Turn parent must be a user message")
            if turn["tip_id"] is not None:
                tip = self.node(turn["tip_id"])
                if tip["metadata"]["chat"].get("turn_id") != turn["id"]:
                    raise ValueError("Turn tip belongs to another turn")
        active = self.state["active_turn_id"]
        if active is not None and active not in turn_ids:
            raise ValueError("Unknown active turn")
        if active is not None and self.turn(active)["status"] != "running":
            raise ValueError("Active turn must be running")

    def node(self, message_id: str) -> Dict[str, Any]:
        try:
            return self.by_id[message_id]
        except (KeyError, TypeError) as exc:
            raise ValueError("Unknown message") from exc

    def path(self, leaf_id: Optional[str] = None, *, latest: bool = True) -> list:
        if not latest and leaf_id is None:
            raise ValueError("Viewing a branch requires its leaf identity")
        leaf = self.state["latest_leaf_id"] if latest else leaf_id
        result = []
        while leaf is not None:
            message = self.node(leaf)
            result.append(deepcopy(message))
            leaf = message["metadata"]["chat"]["parent_id"]
        return result[::-1]

    def check_revision(self, expected: Optional[int]) -> None:
        if expected is not None and expected != self.state["revision"]:
            raise TreeConflict("Chat revision changed")

    def _touch(self) -> None:
        self.state["revision"] += 1

    def _append(self, message: Dict, parent_id: Optional[str], turn_id=None) -> Dict:
        if parent_id is not None:
            self.node(parent_id)
        stored = deepcopy(message)
        if stored.get("message_id") is None:
            stored["message_id"] = identifier()
        if not isinstance(stored["message_id"], str) or not stored["message_id"]:
            raise ValueError("Invalid message identity")
        if stored["message_id"] in self.by_id:
            raise TreeConflict("Message identity already exists")
        if stored.get("role") not in {"user", "assistant", "tool"}:
            raise ValueError("Unsupported Chat message role")
        stored.setdefault("timestamp", datetime.now().isoformat())
        stored.setdefault("metadata", {})["chat"] = {
            "parent_id": parent_id, "turn_id": turn_id,
        }
        self.messages.append(stored)
        self.by_id[stored["message_id"]] = stored
        self.state["latest_leaf_id"] = stored["message_id"]
        self._touch()
        return deepcopy(stored)

    def begin_turn(self, *, parent_id: Optional[str], user_message: Optional[Dict] = None,
                   regenerate_turn_id: Optional[str] = None) -> Dict:
        if self.state["active_turn_id"]:
            raise TreeConflict("A Chat generation is already active")
        if regenerate_turn_id:
            previous = self.turn(regenerate_turn_id)
            parent_id = previous["parent_id"]
            if user_message is not None:
                raise ValueError("Regeneration cannot append a user message")
        elif user_message is None:
            raise ValueError("A new turn needs a user message")
        if user_message is not None:
            if user_message.get("role") != "user":
                raise ValueError("Expected a user message")
            parent_id = self._append(user_message, parent_id)["message_id"]
        if parent_id is None or self.node(parent_id)["role"] != "user":
            raise ValueError("A turn must start from a user message")
        turn = {"id": identifier("turn"), "parent_id": parent_id,
                "tip_id": None, "status": "running"}
        self.state["turns"].append(turn)
        self.state["active_turn_id"] = turn["id"]
        self.state["latest_leaf_id"] = parent_id
        self._touch()
        return deepcopy(turn)

    def turn(self, turn_id: str) -> Dict:
        for turn in self.state["turns"]:
            if turn["id"] == turn_id:
                return turn
        raise ValueError("Unknown turn")

    def append_to_turn(self, turn_id: str, message: Dict) -> Dict:
        turn = self.turn(turn_id)
        if self.state["active_turn_id"] != turn_id or turn["status"] != "running":
            raise TreeConflict("Generation no longer owns this turn")
        parent_id = turn["tip_id"] or turn["parent_id"]
        stored = self._append(message, parent_id, turn_id)
        turn["tip_id"] = stored["message_id"]
        return stored

    def finish_turn(self, turn_id: str, status: str = "complete") -> None:
        if status not in {"complete", "stopped", "failed"}:
            raise ValueError("Invalid turn status")
        turn = self.turn(turn_id)
        if turn["status"] != "running":
            if turn["status"] == status:
                return
            raise TreeConflict("Turn already finished")
        if self.state["active_turn_id"] != turn_id:
            raise TreeConflict("Generation no longer owns this turn")
        turn["status"] = status
        self.state["active_turn_id"] = None
        self._touch()

    def edit(self, message_id: str, content: Any) -> Dict:
        message = self.node(message_id)
        if message["role"] not in {"user", "assistant"}:
            raise ValueError("Tool messages cannot be edited")
        # Never accept a whole replacement message: callers cannot edit protocol
        # identities, tool arguments/results, reasoning blocks or branch links.
        if not isinstance(content, str):
            raise ValueError("Text editing requires a string; media are unchanged")
        if message.get("metadata", {}).get("streaming") is True:
            raise TreeConflict("Streaming content cannot be edited")
        if isinstance(message.get("content"), list):
            parts = [p for p in message["content"] if p.get("type") != "text"]
            message["content"] = [{"type": "text", "text": content}] + parts
        else:
            message["content"] = content
        message.setdefault("metadata", {})["edited_at"] = datetime.now().isoformat()
        # A summary can cover a shared ancestor; invalidate only affected paths.
        for summary in self.state.get("summaries", []):
            if message_id in summary.get("message_ids", []):
                summary["valid"] = False
        self._touch()
        return deepcopy(message)

    def fork_user(self, message_id: str, content: Any) -> Dict:
        source = self.node(message_id)
        if source["role"] != "user":
            raise ValueError("Expected a user message")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Edit-and-send requires nonempty text")
        replacement = deepcopy(source)
        replacement["message_id"] = identifier()
        if isinstance(source.get("content"), list):
            parts = [p for p in source["content"] if p.get("type") != "text"]
            replacement["content"] = [{"type": "text", "text": content}] + parts
        else:
            replacement["content"] = content
        replacement["timestamp"] = datetime.now().isoformat()
        replacement["metadata"] = {
            k: v for k, v in replacement.get("metadata", {}).items()
            if k not in {"chat", "work_timer", "token_speed", "edited_at"}
        }
        return self.begin_turn(parent_id=source["metadata"]["chat"]["parent_id"],
                               user_message=replacement)

    def siblings(self, message_id: str) -> list:
        message = self.node(message_id)
        parent = message["metadata"]["chat"]["parent_id"]
        return [m["message_id"] for m in self.messages
                if m["role"] == message["role"]
                and m["metadata"]["chat"]["parent_id"] == parent]
