"""Privacy-preserving CrossPet v1 projection; no conversation text or tool arguments."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import time

EVENTS = frozenset({
    "api_request_start", "thinking_start", "text_start", "tool_start", "update_action",
    "tool_approval_required", "tool_approval_resolved", "plan_approval_required",
    "plan_approval_resolved", "user_questions_required", "user_questions_resolved",
    "compression_state", "compression_started", "compression_finished", "error",
    "task_complete", "task_stopped", "crosspet_start", "crosspet_finish",
})
WAIT_EVENTS = {"tool_approval_required", "plan_approval_required", "user_questions_required"}
RESOLVE_EVENTS = {"tool_approval_resolved", "plan_approval_resolved", "user_questions_resolved"}


def session_key(username: str, workspace: str, conversation: str) -> str:
    identity = json.dumps([username, workspace, conversation.removeprefix("conv_")])
    return hashlib.sha256(identity.encode()).hexdigest()[:32]


def character_for_model(model: str) -> str:
    name = model.lower()
    for character, terms in (
        ("glm", ("glm", "zhipu", "z.ai")), ("kimi", ("kimi", "moonshot")),
        ("deepseek", ("deepseek",)), ("claude", ("claude", "anthropic")),
        ("gemini", ("gemini",)), ("gpt", ("gpt", "codex", "openai/", "o1", "o3", "o4")),
    ):
        if any(term in name for term in terms):
            return character
    return "current"


def tool_pose(name: str) -> str:
    name = name.lower()
    if name in {"ask_user", "submit_plan"}:
        return "asking"
    if name.startswith("mcp__playwright__") or name in {"web_search", "extract_webpage", "save_webpage"}:
        return "searching"
    if any(word in name for word in ("image_gen", "generate_image", "video_gen")):
        return "drawing"
    if name in {"create_sub_agent", "ask_sub_agent", "send_message_to_sub_agent", "ask_other_agent", "ask_master"}:
        return "delegating"
    if name.startswith(("todo_", "get_sub_agent", "terminate_sub_agent")):
        return "delegating"
    if name in {"run_command", "terminal_input", "terminal_session", "terminal_snapshot", "sleep"}:
        return "running"
    if name.startswith(("read", "view", "recall", "search", "conversation_")):
        return "reading"
    if name.startswith(("write", "edit", "update_memory", "update_project_memory", "create_skill")):
        return "writing"
    return "running"


@dataclass
class SessionState:
    model: str = ""
    task_id: str = ""
    pose: str = "thinking"
    event: str = "UserPromptSubmit"
    tool: str = ""
    ts: float = field(default_factory=time.time)
    revision: int = 0
    tools: set[str] = field(default_factory=set)
    waits: set[str] = field(default_factory=set)
    last_tool_pose: str = ""
    last_tool_ts: float = 0
    finished: str = ""
    background: bool = False
    end_error: bool = False
    after: str = ""
    after_ts: float = 0

    def change(self, pose: str, event: str, now: float) -> None:
        self.pose, self.event, self.ts = pose, event, now
        self.revision += 1
        self.after, self.after_ts = "", 0

    def apply(self, kind: str, data: dict, *, task_id: str, model: str, now: float) -> None:
        if self.task_id != task_id:
            self.task_id = task_id
            self.tools.clear()
            self.waits.clear()
            self.finished = ""
            self.end_error = False
            self.last_tool_pose, self.last_tool_ts = "", 0
            self.change("thinking", "UserPromptSubmit", now)
        self.model = model or self.model
        if kind in WAIT_EVENTS:
            self.waits.add(kind.removesuffix("_required"))
            self.change("asking", "PermissionRequest", now)
        elif kind in RESOLVE_EVENTS:
            self.waits.discard(kind.removesuffix("_resolved"))
            self.change("asking" if self.waits else "thinking", "BeforeModel", now)
        elif kind == "tool_start":
            self.tool = str(data.get("name") or "")[:160]
            self.tools.add(str(data.get("id") or data.get("idx")))
            self.last_tool_pose, self.last_tool_ts = tool_pose(self.tool), now
            self.change("asking" if self.waits else self.last_tool_pose, "PreToolUse", now)
        elif kind == "update_action":
            status = data.get("status")
            if status in {"error", "failed"}:
                self.change("asking" if self.waits else "oops", "PostToolUseFailure", now)
            elif status in {"success", "completed"} and not self.waits:
                self.change("thinking", "PostToolUse", now)
                self.after, self.after_ts = self.last_tool_pose, self.last_tool_ts
        elif kind in {"compression_state", "compression_started"}:
            in_progress = data.get("in_progress", True)
            pose = "asking" if self.waits else "compact" if in_progress else "thinking"
            self.change(pose, "PreCompact" if in_progress else "PostCompact", now)
        elif kind == "compression_finished":
            self.change("asking" if self.waits else "thinking", "PostCompact", now)
        elif kind == "error" and not data.get("retry"):
            self.change("asking" if self.waits else "oops", "PostToolUseFailure", now)
        elif kind == "task_complete":
            self.end_error = bool(data.get("error"))
        elif kind == "task_stopped":
            self.finish("stopped", now)
        elif kind == "crosspet_finish":
            self.finish(str(data.get("status") or "failed"), now)
        elif kind in {"api_request_start", "thinking_start", "text_start", "crosspet_start"} and not self.waits:
            self.change("thinking", "BeforeModel", now)
            if now - self.last_tool_ts < 3:
                self.after, self.after_ts = self.last_tool_pose, self.last_tool_ts
        # task_complete is a response boundary, not proof that background work has ended.

    def finish(self, status: str, now: float) -> None:
        self.finished = status
        self.waits.clear()
        if self.background:
            self.change("delegating", "SubagentStart", now)
        else:
            pose = ("proud" if len(self.tools) >= 8 else "happy") if status == "succeeded" and not self.end_error else (
                "surprised" if status in {"stopped", "canceled", "cancelled"} else "oops")
            self.change(pose, "Stop", now)

    def activity(self, background: bool, now: float) -> None:
        if self.background == background:
            return
        self.background = background
        if self.finished:
            self.finish(self.finished, now)

    def payload(self, now: float) -> dict:
        active = not self.finished or self.background
        value = {
            "character": character_for_model(self.model), "model": self.model[:160],
            "pose": self.pose, "event": self.event, "tool": self.tool,
            "ts": now if active else self.ts, "event_ts": self.ts,
            "revision": self.revision, "active": active, "tools": len(self.tools),
        }
        if self.after:
            value.update(after=self.after, afterTs=self.after_ts)
        return value
