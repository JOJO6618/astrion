"""Single-process registry and atomic persistence for resumable child runtimes."""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATE_FILENAME = "sub_agent_runtime.json"


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class RuntimeRegistry:
    tasks: dict = field(default_factory=dict)
    instances: dict = field(default_factory=dict)
    running: dict = field(default_factory=dict)
    states: dict = field(default_factory=dict)
    lock: Any = field(default_factory=threading.RLock)
    revive_lock: Any = field(default_factory=threading.RLock)


_REGISTRIES: dict[str, RuntimeRegistry] = {}
_LOCK = threading.RLock()


def get_registry(data_dir: Path) -> RuntimeRegistry:
    key = str(data_dir)
    with _LOCK:
        if key not in _REGISTRIES:
            path = data_dir / STATE_FILENAME
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if payload and payload.get("version") != 1:
                raise ValueError("Unsupported child runtime schema")
            registry = RuntimeRegistry(tasks=payload.get("tasks") or {})
            from modules.multi_agent.state import MultiAgentState
            for conversation_id, snapshot in (payload.get("states") or {}).items():
                registry.states[conversation_id] = MultiAgentState.from_snapshot(snapshot)
            _REGISTRIES[key] = registry
        return _REGISTRIES[key]


def save_registry(data_dir: Path, registry: RuntimeRegistry) -> None:
    with registry.lock:
        atomic_json(data_dir / STATE_FILENAME, {
            "version": 1,
            "tasks": registry.tasks,
            "states": {key: state.to_snapshot() for key, state in registry.states.items()},
        })
