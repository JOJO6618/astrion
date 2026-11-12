"""Read-before-edit exemption and stale-write protection for injected memory."""
from __future__ import annotations

import threading
from modules.execution_plane.access import get_execution_backend
from modules.workspace_memory import SNAPSHOT_KEY, PROMPT_KEY, content_version
from modules.system_prompt_snapshot import SYSTEM_PROMPT_KEY

_LOCKS: dict[str, threading.RLock] = {}
_LOCK = threading.Lock()


def injected_memory(owner, path) -> dict | None:
    cm = getattr(owner, "context_manager", None)
    metadata = getattr(cm, "conversation_metadata", {}) or {}
    snapshot = metadata.get(SNAPSHOT_KEY)
    frozen = metadata.get(SYSTEM_PROMPT_KEY) or metadata.get(PROMPT_KEY)
    if not isinstance(snapshot, dict) or not frozen:
        return None
    if (not path or not isinstance(snapshot.get("path"), str)
            or not snapshot["path"] or not isinstance(snapshot.get("version"), str)):
        return None
    files = get_execution_backend(owner).files
    target = files.resolve_path(str(path), access="write")
    expected = files.resolve_path(snapshot["path"], access="write")
    if (target.get("success") and expected.get("success") and target.get("path")
            and target.get("path") == expected.get("path")):
        return snapshot
    return None


def write_injected_memory(owner, tool_name: str, arguments: dict) -> dict | None:
    """Use normal backend tools, checking known contents under a per-file lock.

    Conflicts return the latest contents to the model without silently replacing
    its frozen system prompt. This is tool feedback, not a live memory refresh.
    """
    snapshot = injected_memory(owner, arguments.get("file_path"))
    if snapshot is None:
        return None
    backend = get_execution_backend(owner)
    path = snapshot["path"]
    with _LOCK:
        lock = _LOCKS.setdefault(f"{owner.project_path}:{path}", threading.RLock())
    with lock:
        info = backend.files.inspect_path(path)
        if not info.get("success"):
            return {"success": False, "error": info.get("error") or "无法检查记忆文件"}
        current = ""
        if info.get("exists"):
            result = backend.files.read_file(path)
            if not result.get("success"):
                return result
            current = result.get("content")
            if not isinstance(current, str):
                return {"success": False, "error": "无法获取完整记忆内容"}
        cm = owner.context_manager
        version = content_version(current)
        known = cm.conversation_metadata.get("workspace_memory_write_version", snapshot["version"])
        if version != known:
            cm._set_meta_flag("workspace_memory_write_version", version)
            return {"success": False, "error": "MEMORY.md 已被其他对话修改，请基于最新内容合并后重试。",
                    "enforcement": "workspace_memory_conflict", "path": path,
                    "current_content": current}
        if tool_name == "write_file":
            result = backend.write_file(path, arguments.get("content", ""),
                                        mode="a" if arguments.get("append") else "w")
        else:
            result = backend.edit_file(path, arguments["replacements"])
        if result.get("success"):
            updated = backend.files.read_file(path)
            if not updated.get("success") or not isinstance(updated.get("content"), str):
                raise RuntimeError("记忆已写入，但无法核对写后版本")
            cm._set_meta_flag("workspace_memory_write_version", content_version(updated["content"]))
        return result
