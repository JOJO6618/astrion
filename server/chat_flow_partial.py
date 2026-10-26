"""Keep received prose on stop, while never persisting partial tool calls."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class PartialResponse:
    message_id: str
    text: str = ""
    thinking: str = ""
    has_tools: bool = False
    committed: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def reset(self) -> None:
        self.text = self.thinking = ""
        self.has_tools = False


def commit_pending_response(terminal, sender) -> bool:
    """Called at a cancellation boundary before publishing the final task view.

    Completed encrypted reasoning items belong to the normal commit path. This
    path saves displayable reasoning only and never replays incomplete items.
    """
    partial = getattr(terminal, "_stream_partial", None)
    if not partial or partial.committed or partial.has_tools:
        return False
    if not (partial.text or partial.thinking):
        return False
    context = getattr(terminal, "context_manager", None)
    if context is None:
        return False
    metadata = {**partial.metadata, "stopped": True, "status": "stopped"}
    context.add_conversation(
        "assistant", partial.text, reasoning_content=partial.thinking,
        message_id=partial.message_id, metadata=metadata,
    )
    partial.committed = True
    context.auto_save_conversation(force=True)
    if partial.thinking:
        sender("thinking_end", {"full_content": partial.thinking, "stopped": True})
    if partial.text:
        sender("text_end", {"full_content": partial.text, "stopped": True})
    return True
