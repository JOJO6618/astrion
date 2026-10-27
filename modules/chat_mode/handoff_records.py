"""Stable identities for each real suggestion, independent of presentation timers."""
from copy import deepcopy
from difflib import SequenceMatcher
from html import unescape
import re
import time
from uuid import uuid4

from modules.chat_mode.tree import MessageTree

TAG = re.compile(r'<suggest_agent\b[^>]*\/>', re.I)
_REASON = re.compile(r'\breason\s*=\s*([\"\'])(.*?)\1', re.I | re.S)
_CODE = re.compile(r'(?m)^ {0,3}(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^ {0,3}\1[~`]*[ \t]*(?:\n|$)|\Z)|(`+)[\s\S]*?\2')
TERMINAL = {"completed", "declined", "superseded"}
ACTIONABLE = {"pending", "selecting", "failed"}


def tags(content):
    if isinstance(content, list):
        content = "\n".join(p.get("text", "") for p in content if p.get("type") == "text")
    if not isinstance(content, str):
        return []
    # Preserve offsets; examples inside fenced/inline code aren't actions.
    masked = _CODE.sub(lambda m: " " * len(m.group()), content)
    return [m.group() for m in TAG.finditer(masked)]


def pause(state):
    state.update(countdown_enabled=False, deadline=None)


def resolve(entries, identity):
    if identity in entries:
        return entries[identity]
    matches = [state for state in entries.values()
               if state.get("id") == identity or state.get("message_id") == identity]
    if len(matches) == 1:
        return matches[0]
    raise FileNotFoundError("Agent suggestion unavailable or ambiguous")


def sync(document, *, fresh_ids=(), presentation_id=None):
    """Called only in explicit write transactions, never by conversation reads."""
    tree = MessageTree(document)
    chat = tree.state
    old_entries = chat.get("handoffs", {})
    entries = {}
    count = max(chat.get("handoff_count", 0), len(old_entries))
    for index, (key, value) in enumerate(old_entries.items(), 1):
        state = deepcopy(value)
        state.setdefault("id", f"handoff_{uuid4().hex}")
        state.setdefault("message_id", key)
        state.setdefault("sequence", index)
        state.setdefault("tag_index", 0)
        state.setdefault("present", True)
        state.setdefault("created_at", time.time())
        # Old registrations have no session owner; never re-arm them on read.
        if not state.get("presentation_id"):
            pause(state)
        entries[state["id"]] = state
        count = max(count, state["sequence"])
    path = tree.path()
    latest_ids = {item["message_id"] for item in path}
    for message in document["messages"]:
        if message["role"] != "assistant":
            continue
        message_id = message["message_id"]
        if (chat.get("active_turn_id") and message_id not in fresh_ids
                and message.get("metadata", {}).get("chat", {}).get("turn_id") == chat["active_turn_id"]):
            continue
        current_tags = tags(message.get("content"))
        previous = sorted((s for s in entries.values()
                           if s["message_id"] == message_id and s.get("present", True)),
                          key=lambda s: s["tag_index"])
        # Upgrade the original one-record-per-message schema without new IDs.
        for state in previous:
            state.setdefault("tag", current_tags[0] if current_tags else "")
        mapping = {}
        matcher = SequenceMatcher(a=[s["tag"] for s in previous], b=current_tags, autojunk=False)
        for operation, i, end_i, j, end_j in matcher.get_opcodes():
            if operation in {"equal", "replace"}:
                for before, after in zip(range(i, end_i), range(j, end_j)):
                    mapping[after] = previous[before]
        kept = {s["id"] for s in mapping.values()}
        for state in previous:
            if state["id"] not in kept:
                state["present"] = False
                pause(state)
                if state["status"] not in TERMINAL:
                    state.update(status="superseded", decision_source="edited", decided_at=time.time())
        for tag_index, raw in enumerate(current_tags):
            state = mapping.get(tag_index)
            if state is None:
                state = next((s for s in entries.values()
                              if s["message_id"] == message_id and not s.get("present", True)
                              and s.get("tag") == raw), None)
            if state is None:
                count += 1
                identity = f"handoff_{uuid4().hex}"
                fresh = message_id in fresh_ids
                status = "pending"
                # Missing legacy records with a real continuation are history,
                # not fresh invitations. Other branches remain non-actionable.
                if not fresh and message_id in latest_ids:
                    position = next(i for i, item in enumerate(path) if item["message_id"] == message_id)
                    if any(m["role"] == "user" and m.get("metadata", {}).get("starts_work", True)
                           for m in path[position + 1:]):
                        status = "declined"
                state = {"id": identity, "message_id": message_id, "sequence": count,
                         "status": status, "created_at": time.time(),
                         "leaf_id": chat["latest_leaf_id"] if message_id in latest_ids else message_id,
                         "presentation_id": presentation_id if fresh else None,
                         "countdown_enabled": bool(fresh and presentation_id),
                         "deadline": time.time() + 30 if fresh and presentation_id else None}
                entries[identity] = state
            reason = _REASON.search(raw)
            state.update(tag_index=tag_index, tag=raw, present=True,
                         reason=unescape(reason.group(2))[:500] if reason else "")
    positions = {item["message_id"]: i for i, item in enumerate(path)}
    for state in entries.values():
        position = positions.get(state["message_id"])
        if (state["status"] in ACTIONABLE and position is not None
                and any(m["role"] == "user" and m.get("metadata", {}).get("starts_work", True)
                        for m in path[position + 1:])):
            state.update(status="declined", decision_source="historical_message")
            pause(state)
    chat.update(handoffs=entries, handoff_count=count)
    return entries


def continue_after_send(tree, previous_leaf):
    """The caller has already accepted the new tree turn in the same transaction."""
    path_ids = {item["message_id"] for item in tree.path()}
    for state in tree.state.get("handoffs", {}).values():
        if state["status"] not in ACTIONABLE:
            continue
        continued = state.get("leaf_id") == previous_leaf and state.get("message_id") in path_ids
        state.update(status="declined" if continued else "superseded",
                     decision_source="message" if continued else "branch",
                     decided_at=time.time())
        pause(state)


def detach(document):
    """Copy/import keeps decisions but drops all live ownership and retry jobs."""
    entries = sync(document)
    document["metadata"]["chat"].pop("handoff_presentation_id", None)
    detached = {}
    for original in entries.values():
        state = deepcopy(original)
        state["source_handoff_id"] = state["id"]
        state["id"] = f"handoff_{uuid4().hex}"
        state.pop("presentation_id", None)
        pause(state)
        if state["status"] in {"preparing", "selecting", "failed"}:
            state.update(status="pending", error="")
        for key in ("operation_id", "task_id", "agent_task_id"):
            state.pop(key, None)
        detached[state["id"]] = state
    document["metadata"]["chat"]["handoffs"] = detached


def complete(state, document, **fields):
    state.update(status="completed", decided_at=time.time(), **fields)
    pause(state)
    for sibling in document["metadata"]["chat"].get("handoffs", {}).values():
        if sibling["id"] != state["id"] and sibling["status"] in ACTIONABLE and sibling.get("leaf_id") == state.get("leaf_id"):
            sibling.update(status="superseded", decision_source="sibling_switched", decided_at=time.time())
            pause(sibling)
