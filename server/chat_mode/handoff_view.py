"""Public handoff views never expose server-side storage paths in Docker."""
from copy import deepcopy
import re


_PUBLIC_FIELDS = {
    "id", "message_id", "status", "reason", "deadline", "error",
    "sequence", "tag_index", "present", "created_at", "decided_at", "decision_source",
    "countdown_enabled", "presentation_id", "leaf_id",
    "agent_conversation_id", "workspace_id", "agent_url",
    "target_workspace_id", "selection",
}
# Keep URLs intact, but strip absolute filesystem paths from exception text.
# Consume spaces too: filenames in OSError messages may contain whitespace.
_HOST_PATH = re.compile(r"(?<![\w:/\\])(?:[A-Za-z]:[\\/]|/(?!/))[^\r\n\"'<>]*")


def public_error(error, *, host_mode: bool) -> str:
    text = str(error or "")
    return text if host_mode else _HOST_PATH.sub("[server path]", text)


def public_handoff(state: dict, *, host_mode: bool) -> dict:
    result = {key: deepcopy(value) for key, value in state.items() if key in _PUBLIC_FIELDS}
    if "error" in result:
        result["error"] = public_error(result["error"], host_mode=host_mode)
    if not host_mode and isinstance(result.get("selection"), dict):
        result["selection"].pop("project_path", None)
    return result


def public_document(document: dict, *, host_mode: bool) -> dict:
    """Sanitize only handoff metadata, without rewriting the authoritative tree."""
    metadata = document.get("metadata") or {}
    chat = metadata.get("chat") or {}
    states = chat.get("handoffs")
    if not isinstance(states, dict):
        return document
    return {**document, "metadata": {**metadata, "chat": {**chat, "handoffs": {
        key: public_handoff(value, host_mode=host_mode) for key, value in states.items()
    }}}}
