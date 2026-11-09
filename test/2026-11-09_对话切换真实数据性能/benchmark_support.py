"""Real-data performance fixtures. Originals are read-only; writes stay in scratch."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from types import SimpleNamespace
from collections import deque

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT / "_experiments/conversation-switch-benchmark-20261109"
SOURCE = Path("/Users/jojo/.astrion/astrion-desktop/host/data/conversations/agents")
SAMPLE_IDS = [
    "conv_20261107_025348_451",
    "conv_20261031_170429_608",
    "conv_20261021_211531_333",
]


def initialize():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    isolated = SCRATCH / "isolated"
    for name, value in {
        "ASTRION_IGNORE_DOTENV": "1",
        "ASTRION_DATA_ROOT": str(isolated),
        "DATA_DIR": str(isolated / "data"),
        "LOGS_DIR": str(isolated / "logs"),
        "USER_SPACE_DIR": str(isolated / "users"),
        "API_USER_SPACE_DIR": str(isolated / "api-users"),
        "TERMINAL_SANDBOX_MODE": "host",
    }.items():
        os.environ[name] = value
    sys.path.insert(0, str(ROOT))


class TimedLock:
    """RLock-compatible recorder of outer acquisition waits and hold durations."""

    def __init__(self):
        self.lock = threading.RLock()
        self.local = threading.local()
        self.rows = []

    def acquire(self, *args, **kwargs):
        started = time.perf_counter()
        acquired = self.lock.acquire(*args, **kwargs)
        if acquired:
            depth = getattr(self.local, "depth", 0)
            if not depth:
                self.local.started = time.perf_counter()
                self.local.wait_ms = (self.local.started - started) * 1000
            self.local.depth = depth + 1
        return acquired

    def release(self):
        self.local.depth -= 1
        if not self.local.depth:
            self.rows.append({
                "thread": threading.current_thread().name,
                "wait_ms": round(self.local.wait_ms, 3),
                "hold_ms": round((time.perf_counter() - self.local.started) * 1000, 3),
            })
        self.lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


def stats(values):
    values = sorted(values)
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "median_ms": round(values[len(values) // 2], 3),
        "max_ms": round(values[-1], 3),
        "min_ms": round(values[0], 3),
    }


def timed(call):
    started = time.perf_counter()
    value = call()
    return value, (time.perf_counter() - started) * 1000


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_samples():
    from utils.conversation_manager import ConversationManager
    samples = []
    base = SCRATCH / "storage"
    directory = base / "conversations"
    directory.mkdir(parents=True, exist_ok=True)
    for cid in SAMPLE_IDS:
        source = SOURCE / f"{cid}.json"
        raw = source.read_bytes()
        target = directory / source.name
        target.write_bytes(raw)
        samples.append({"id": cid, "document": json.loads(raw),
                        "original_sha256": hashlib.sha256(raw).hexdigest(),
                        "file_bytes": len(raw), "source": source})
    manager = ConversationManager(base_dir=str(base), read_only=True)
    manager.read_only = False
    # Empty isolated deployment config resolves to this root (no production index).
    assert manager.conversations_dir.resolve() == directory.resolve()
    return manager, samples


def segments(messages):
    anchors = [i for i, msg in enumerate(messages)
               if msg.get("role") == "user"
               and ((msg.get("metadata") or {}).get("starts_work") is True
                    or (msg.get("metadata") or {}).get("message_source") == "user")]
    if not anchors:
        anchors = [0]
    return [(start, anchors[pos + 1] if pos + 1 < len(anchors) else len(messages))
            for pos, start in enumerate(anchors)]


def reconstruct(messages, cid, model):
    """Replay persisted semantic messages; unavailable monitor/chunk timing is omitted."""
    from server.conversation_view.projector import RuntimeProjection
    projection = RuntimeProjection(f"benchmark-{cid}", cid, model)
    index = 0
    def emit(kind, data):
        nonlocal index
        projection.apply({"idx": index, "type": kind, "data": data, "ts": time.time()})
        index += 1
    for message in messages:
        role = message.get("role")
        if role == "user":
            emit("user_message", message)
        elif role == "assistant":
            emit("api_request_start", {})
            response = message.get("message_id")
            if message.get("reasoning_content"):
                emit("thinking_end", {"full_content": message["reasoning_content"],
                                       "response_message_id": response})
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                emit("text_start", {"response_message_id": response})
                emit("text_chunk", {"content": content, "response_message_id": response})
                emit("text_end", {"response_message_id": response})
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {}
                emit("tool_start", {"id": call.get("id"), "name": function.get("name", ""),
                                    "arguments": arguments})
        elif role == "tool":
            try:
                result = json.loads(message.get("content") or "{}")
            except (ValueError, TypeError):
                result = (message.get("metadata") or {}).get("tool_payload")
                if result is None:
                    result = {"output": message.get("content", ""), "success": True}
            emit("tool_update_action", {"id": message.get("tool_call_id"),
                                        "status": "completed", "result": result})
        elif role == "system":
            emit("system_message", message)
    return projection, index


def fixture_manager(sample):
    from server.conversation_view.snapshots import TaskViewMixin
    messages = sample["document"]["messages"]
    ranges = segments(messages)
    start, end = max(ranges, key=lambda r: sum(
        len(m.get("tool_calls") or []) for m in messages[r[0]:r[1]]))
    projection, cursor = reconstruct(messages[start:end], sample["id"],
                                     sample["document"].get("metadata", {}).get("model_key"))
    rec = SimpleNamespace(
        task_id=f"benchmark-{sample['id']}", username="benchmark", workspace_id="benchmark",
        conversation_id=sample["id"], model_key=None, task_type="chat",
        task_params=SimpleNamespace(goal_mode=False), goal_progress=None,
        status="running", created_at=1, updated_at=1, display_valid=True,
        display_base=messages[:start], display_projection=projection,
        display_revision="benchmark-revision", next_event_idx=cursor,
        events=deque(maxlen=20000), display_context=None,
    )
    class FixtureManager(TaskViewMixin):
        def __init__(self):
            self._lock = threading.RLock()
            self._tasks = {rec.task_id: rec}
    return FixtureManager(), rec, {"start": start, "end": end,
        "messages": end - start, "projected_messages": len(projection.messages),
        "actions": sum(len(m.get("actions", [])) for m in projection.messages),
        "max_actions_in_one_message": max((len(m.get("actions", [])) for m in projection.messages), default=0)}


def export_frontend(samples):
    # Sensitive fixtures live only in ignored scratch, never in tracked test files.
    payload = [{"id": s["id"], "messages": s["document"]["messages"]} for s in samples]
    path = SCRATCH / "frontend-input.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
