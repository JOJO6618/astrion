"""Import a detached conversation, preserving its original message prefix."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .archive import validate_document
from .assets import restore, safe_file


def source_info(workspace, *, host_mode: bool) -> dict:
    return {"workspace_id": workspace.workspace_id,
            "workspace_name": getattr(workspace, "label", None) or workspace.workspace_id,
            "project_path": str(workspace.project_path) if host_mode else "/workspace",
            "environment": "host" if host_mode else "docker"}


def read_document(manager, conversation_id: str) -> dict:
    path = manager._get_conversation_file_path(conversation_id)
    with manager._io_lock, path.open("r", encoding="utf-8") as stream:
        document = json.load(stream)
    if document.get("id") != conversation_id:
        raise ValueError("Conversation identity does not match its file")
    validate_document(document)
    return document


def history_fingerprint(messages: list) -> str:
    return hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def verify_import(document: dict, source_id: str, document_sha256: str, operation_id: str):
    """A receipt must still point to this import and its unchanged history prefix."""
    transfer = document.get("metadata", {}).get("conversation_transfer") or {}
    length = transfer.get("history_length")
    if (transfer.get("source_conversation_id") != source_id
            or transfer.get("document_sha256") != document_sha256
            or transfer.get("operation_id") != operation_id
            or not isinstance(length, int) or length < 0
            or len(document["messages"]) < length
            or history_fingerprint(document["messages"][:length]) != transfer.get("history_sha256")):
        raise RuntimeError("The migration destination has changed; the source is preserved")


def _notice(source: dict, target: dict, resources: list, modes: dict) -> str:
    text = ["[系统通知|conversation_migrated]", "本对话已迁移，历史对话内容保持原样。",
            f"原工作区：{source.get('workspace_name') or source.get('workspace_id') or 'Chat'}",
            f"原工作区位置：{source.get('project_path') or '无'}",
            f"原运行环境：{source.get('environment') or '未知'}",
            f"当前工作区：{target.get('workspace_name') or 'Chat'}",
            f"当前工作区位置：{target.get('project_path') or '无'}",
            f"当前运行环境：{target.get('environment') or '未知'}"]
    if modes:
        text += [f"当前工作模式：{modes['work_mode']}；权限：{modes['permission_mode']}；"
                 f"执行环境：{modes['execution_mode']}；网络：{modes['network_permission']}。"]
    media = sum(row["kind"] in {"image", "video"} for row in resources)
    materials = sum(row["kind"] == "material" for row in resources)
    text += [f"本次携带 {media} 项发送过或阅读过的图片/视频，以及 {materials} 项摘要或交接材料。",
             "普通上传附件、项目源码及其他工作文件未迁移。",
             "历史路径、文件存在情况、终端、进程、审批及子智能体状态属于迁移前的记录。"
             "旧工作区规范与冻结提示中的环境描述属于原环境，当前实际能力与可访问范围以目标环境为准。",
             "旧图片/视频及材料引用由应用资源映射解析，无需改写历史；"
             "其他旧路径使用前请先确认当前位置。后续从原有进度继续。"]
    return "\n".join(text)


def import_document(document: dict, manifest: dict, files: dict, manager,
                    *, project: Path | None, target: dict, modes: dict,
                    model_key: str | None = None, operation_id: str | None = None,
                    preserve_id: bool = False):
    """Stage assets, then publish one complete document and its list index."""
    kind = validate_document(document)
    if manager.conversation_kind != kind:
        raise ValueError("Import this file in its original Chat or Agent mode")
    # Retry the exact operation without creating another copy or another notice.
    receipt = None
    target_identity = {name: target.get(name) for name in ("workspace_id", "project_path", "environment")}
    if operation_id:
        key = hashlib.sha256(operation_id.encode()).hexdigest()
        receipt = manager.base_dir / "conversation_imports" / f"{key}.json"
        if receipt.exists():
            saved = json.loads(receipt.read_text(encoding="utf-8"))
            if (saved.get("document_sha256") != manifest.get("document_sha256")
                    or saved.get("target") != target_identity or saved.get("model_key") != model_key):
                raise ValueError("Import operation was reused for a different file, target or model")
            existing = manager.read_conversation(saved["conversation_id"])
            if existing is not None:
                verify_import(existing, document["id"], manifest["document_sha256"], operation_id)
                return existing
    result = deepcopy(document)
    conversation_id = document["id"] if preserve_id else manager._generate_conversation_id()
    if preserve_id and manager._get_conversation_file_path(conversation_id).exists():
        raise ValueError("The target workspace already contains this conversation identity")
    while manager._get_conversation_file_path(conversation_id).exists():
        conversation_id = manager._generate_conversation_id()
    result["id"] = conversation_id
    result["updated_at"] = datetime.now().isoformat()
    metadata = result["metadata"]
    old_transfer = metadata.get("conversation_transfer") or {}
    paths = {}
    try:
        paths = restore(manifest["resources"], files, manager.base_dir, project, conversation_id)
        metadata["conversation_transfer"] = {
            "source_conversation_id": document["id"],
            "source": deepcopy(manifest["source"]), "target": target,
            "document_sha256": manifest.get("document_sha256"), "operation_id": operation_id,
            "history_length": len(document["messages"]),
            "history_sha256": history_fingerprint(document["messages"]),
            "imported_at": result["updated_at"], "paths": paths,
            "previous": {key: value for key, value in old_transfer.items() if key != "paths"},
        }
        if project is not None:
            metadata.update(manager._prepare_project_path_metadata(str(project)))
            metadata.update(modes)
            for name in ("pending_permission_mode", "pending_execution_mode", "pending_work_mode", "pending_network_permission"):
                metadata[name] = None
            for name in ("project_file_tree", "project_statistics", "project_snapshot_at",
                         "compression_resume_payload", "compression_job_id", "compression_error"):
                metadata[name] = None
            metadata["compression_in_progress"] = False
            metadata["compression_stage"] = None
            metadata["compression_mode"] = None
            # Deferred tool definitions are not bundled as current capabilities.
            # Preserve their original snapshot, and let normal tools policy apply.
        if model_key:
            metadata["model_key"] = model_key
        notice = {"role": "user", "content": _notice(manifest["source"], target,
                  manifest["resources"], modes), "message_id": f"msg_{uuid4().hex}",
                  "timestamp": result["updated_at"],
                  "metadata": {"conversation_migration": True, "message_source": "notify",
                               "visibility": "compact", "starts_work": False}}
        if manifest.get("shared_media"):
            notice["content"] += "\n当前迁移共用原媒体库，已发送和阅读的图片/视频继续使用原有媒体引用，无需复制。"
        if kind == "chat":
            from modules.chat_mode.tree import MessageTree
            tree = MessageTree(result)
            if tree.state.get("active_turn_id"):
                raise ValueError("An active Chat turn cannot be imported")
            tree._append(notice, tree.state["latest_leaf_id"])
            tree.state["commands"] = {}
            tree.state["handoffs"] = {}
            result["metadata"]["pending_network_notices"] = []
        else:
            result["messages"].append(notice)
        metadata["total_messages"] = len(result["messages"])
        metadata["total_tools"] = manager._count_tools_in_messages(result["messages"])
        from utils.media_store import MediaStore
        media_store = MediaStore(manager.base_dir)
        for message in result["messages"]:
            references = message.get("media_refs") or (message.get("metadata") or {}).get("media_refs") or []
            if references and message.get("message_id"):
                media_store.link_message_media(conversation_id, message["message_id"], references)
        with manager._io_lock:
            manager._atomic_write_json(manager._get_conversation_file_path(conversation_id), result)
            try:
                manager._update_index(conversation_id, result)
                if receipt:
                    receipt.parent.mkdir(parents=True, exist_ok=True)
                    manager._atomic_write_json(receipt, {"conversation_id": conversation_id,
                        "document_sha256": manifest.get("document_sha256"),
                        "target": target_identity, "model_key": model_key})
            except Exception:
                manager._get_conversation_file_path(conversation_id).unlink(missing_ok=True)
                index = manager._load_index()
                index.pop(conversation_id, None)
                manager._save_index(index)
                raise
        return result
    except Exception:
        root = project if project is not None else manager.base_dir
        directory = safe_file(root, f".astrion/conversation_assets/{conversation_id}")
        if directory.exists():
            shutil.rmtree(directory)
        raise


def retire_source(manager, document: dict, destination: dict):
    """Retain a recovery copy before removing the source from its workspace list."""
    conversation_id = document["id"]
    backup = manager.base_dir / "conversation_transfer_backups" / f"{conversation_id}-{uuid4().hex}.json"
    with manager._io_lock:
        backup.parent.mkdir(parents=True, exist_ok=True)
        manager._atomic_write_json(backup, {"conversation": document, "destination": destination})
        path = manager._get_conversation_file_path(conversation_id)
        index = manager._load_index()
        index.pop(conversation_id, None)
        # Remove index first. If unlink fails, put the original entry back.
        manager._save_index(index)
        try:
            path.unlink()
        except Exception:
            manager._update_index(conversation_id, document)
            raise
