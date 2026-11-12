"""Full-text workspace memory snapshots. No per-request disk refresh."""
from __future__ import annotations

from modules.execution_plane.access import get_execution_backend, is_local_backend
from modules.workspace_memory import (
    ENABLED_KEY, PROMPT_KEY, SNAPSHOT_KEY, content_version, location,
    prepare_directory, register_host_memory,
)

BEGIN = "<!-- astrion-workspace-memory:start -->"
END = "<!-- astrion-workspace-memory:end -->"


def owner_location(owner):
    # No controller filesystem fallback for a bound remote/fake executor.
    if not is_local_backend(owner):
        raise RuntimeError("当前执行后端尚不支持私有工作区记忆位置解析")
    session = getattr(owner, "container_session", None)
    docker = getattr(session, "mode", None) == "docker"
    return location(str(owner.project_path), str(owner.data_dir), docker=docker)


def read_snapshot(owner) -> dict:
    target = owner_location(owner)
    if not target.docker:
        register_host_memory(str(owner.project_path), str(owner.data_dir))
    prepare_directory(target)
    backend = get_execution_backend(owner)
    info = backend.files.inspect_path(target.tool_path)
    if not info.get("success"):
        raise PermissionError(info.get("error") or "无法访问私有项目记忆")
    content = ""
    if info.get("exists"):
        if not info.get("is_file") or info.get("is_symlink"):
            raise PermissionError("MEMORY.md 必须是普通文件")
        result = backend.files.read_file(target.tool_path)
        if not result.get("success"):
            raise PermissionError(result.get("error") or "无法读取私有项目记忆")
        content = result.get("content")
        if not isinstance(content, str):
            raise ValueError("记忆文件未返回完整文本")
    return {"path": target.tool_path, "version": content_version(content),
            "exists": bool(info.get("exists")), "content": content}


def render_snapshot(snapshot: dict, template: str, *, child: bool = False) -> str:
    maintenance = (
        "你是子智能体：这份共享记忆由主智能体维护。发现需要新增或修正的内容时，向主智能体汇报；不要自行修改。"
        if child else
        "全文已注入，视为已读；可以直接用 write_file / edit_file 维护上述文件，无需先调用 read_file。只读／计划模式仍禁止修改。"
    )
    # Substitute the body last: user memory cannot inject template placeholders.
    text = template.replace("{workspace_memory_path}", snapshot["path"])
    text = text.replace("{workspace_memory_maintenance}", maintenance)
    text = text.replace("{workspace_memory_content}", snapshot["content"] or "（当前记忆为空，可直接创建文件。）")
    return f"{BEGIN}\n{text.strip()}\n{END}"


def prepare_main_prompt(owner, *, template_name: str) -> tuple[str, dict]:
    """Prepare content and version data for the complete system's atomic commit."""
    if not owner.context_manager.conversation_metadata.get(ENABLED_KEY):
        return "", {}
    snapshot = read_snapshot(owner)
    text = render_snapshot(snapshot, owner.load_prompt(template_name))
    record = {key: value for key, value in snapshot.items() if key != "content"}
    return text, {
        SNAPSHOT_KEY: record,
        "workspace_memory_write_version": snapshot["version"],
    }


def main_prompt(owner) -> str:
    cm = owner.context_manager
    metadata = cm.conversation_metadata
    if not metadata.get(ENABLED_KEY):
        # Existing conversations acquire this feature at their next compression,
        # rather than changing a frozen prefix on resume or an ordinary turn.
        return ""
    frozen = metadata.get(PROMPT_KEY)
    if isinstance(frozen, str):
        return frozen
    snapshot = read_snapshot(owner)
    text = render_snapshot(snapshot, owner.load_prompt("workspace_memory"))
    record = {key: value for key, value in snapshot.items() if key != "content"}
    updates = {SNAPSHOT_KEY: record, PROMPT_KEY: text,
               "workspace_memory_write_version": snapshot["version"]}
    conversation_id = getattr(cm, "current_conversation_id", None)
    if conversation_id:
        manager = (cm._get_conversation_manager_for_id(conversation_id)
                   if hasattr(cm, "_get_conversation_manager_for_id") else cm.conversation_manager)
        if not manager.update_conversation_metadata(conversation_id, updates):
            raise RuntimeError("无法保存项目记忆快照；本次未注入，避免恢复时读取不同版本")
    metadata.update(updates)
    return text


def child_prompt(owner) -> str:
    from pathlib import Path
    template = (Path(__file__).resolve().parent.parent / "prompts" / "workspace_memory.txt").read_text(encoding="utf-8")
    return render_snapshot(read_snapshot(owner), template, child=True)


def replace_child_memory(system_prompt: str, owner) -> str:
    """Refresh only the private-memory segment on child compression."""
    fresh = child_prompt(owner)
    start = system_prompt.find(BEGIN)
    end = system_prompt.rfind(END) if start >= 0 else -1
    if start >= 0 and end > start:
        return system_prompt[:start] + fresh + system_prompt[end + len(END):]
    return system_prompt.rstrip() + "\n\n" + fresh


def save_child_prompt(path, text: str) -> None:
    """An interrupted refresh must not leave a partial restored system prompt."""
    import os
    import tempfile
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".workspace-memory-", delete=False) as stream:
            temporary = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)
