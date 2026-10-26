from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List
from modules.versioning_manager import ConversationVersioningManager, VersioningError
from modules.shallow_versioning import ShallowVersioningManager
from .utils_common import debug_log


def _should_skip_versioning_for_message(
    *,
    message: str,
    auto_user_message_event: bool,
) -> bool:
    if auto_user_message_event:
        return True
    text = (message or "").strip()
    if not text:
        return True
    if text.startswith("这是一句系统自动发送的user消息"):
        return True
    return False

def _prepare_hidden_versioning_baseline_for_first_input(
    *,
    web_terminal,
    workspace,
    conversation_id: str,
    message: str,
    auto_user_message_event: bool,
) -> None:
    """Ensure first-run baseline is committed (hidden, no checkpoint row)."""
    try:
        if _should_skip_versioning_for_message(
            message=message,
            auto_user_message_event=auto_user_message_event,
        ):
            return
        cm = getattr(getattr(web_terminal, "context_manager", None), "conversation_manager", None)
        if not cm:
            return
        conv_data = cm.load_conversation(conversation_id) or {}
        versioning_meta = ((conv_data.get("metadata") or {}).get("versioning") or {})
        if not bool(versioning_meta.get("enabled", False)):
            return
        backup_mode = str(versioning_meta.get("backup_mode") or "shallow").strip().lower()
        if backup_mode == "full":
            tracking_mode = ConversationVersioningManager.normalize_tracking_mode(
                versioning_meta.get("tracking_mode")
            )
            if (
                tracking_mode == ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION
                and not bool(getattr(web_terminal, "username", None) == "host")
            ):
                return
            manager = ConversationVersioningManager(
                project_path=workspace.project_path,
                data_dir=workspace.data_dir,
                conversation_id=conversation_id,
            )
            baseline = manager.ensure_baseline_for_first_input(tracking_mode=tracking_mode)
            debug_log(
                f"[Versioning][Baseline] conv={conversation_id} "
                f"tracking_mode={tracking_mode} "
                f"created={baseline.get('created')} skipped={baseline.get('skipped')} "
                f"reason={baseline.get('reason')} head={baseline.get('head')}"
            )
        # shallow 模式下不需要完整 workspace baseline，由工具调用前的 track_edit 处理
    except VersioningError as exc:
        debug_log(f"[Versioning] 创建首轮基线失败: {exc}")
    except Exception as exc:
        debug_log(f"[Versioning] 创建首轮基线异常: {exc}")

def _record_hidden_versioning_checkpoint_after_run(
    *,
    web_terminal,
    workspace,
    conversation_id: str,
    message: str,
    message_index: int,
    auto_user_message_event: bool,
    run_status: str = "completed",
) -> None:
    """Hidden snapshot after current manual user input run finished."""
    try:
        if _should_skip_versioning_for_message(
            message=message,
            auto_user_message_event=auto_user_message_event,
        ):
            return
        cm = getattr(getattr(web_terminal, "context_manager", None), "conversation_manager", None)
        if not cm:
            return
        conv_data = cm.load_conversation(conversation_id) or {}
        versioning_meta = ((conv_data.get("metadata") or {}).get("versioning") or {})
        if not bool(versioning_meta.get("enabled", False)):
            return
        tracking_mode = ConversationVersioningManager.normalize_tracking_mode(
            versioning_meta.get("tracking_mode")
        )
        if (
            tracking_mode == ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION
            and not bool(getattr(web_terminal, "username", None) == "host")
        ):
            return
        snapshot_messages = conv_data.get("messages") or []
        # 找到当前用户消息的消息ID（用于浅备份快照）
        message_id = None
        try:
            target_msg = snapshot_messages[int(message_index)] if 0 <= int(message_index) < len(snapshot_messages) else None
            if target_msg and target_msg.get("role") == "user":
                message_id = target_msg.get("message_id") or target_msg.get("id")
        except Exception:
            message_id = None

        backup_mode = str(versioning_meta.get("backup_mode") or "shallow").strip().lower()
        if backup_mode == "full":
            snapshot_payload = {
                "conversation_id": conversation_id,
                "title": conv_data.get("title"),
                "metadata": conv_data.get("metadata") or {},
                "messages": snapshot_messages,
                "message_index": int(message_index),
                "run_status": str(run_status or "completed"),
            }
            manager = ConversationVersioningManager(
                project_path=workspace.project_path,
                data_dir=workspace.data_dir,
                conversation_id=conversation_id,
            )
            row = manager.create_checkpoint(
                message=message,
                message_index=message_index,
                workspace_path=str(workspace.project_path),
                conversation_snapshot=snapshot_payload,
                run_status=str(run_status or "completed"),
                tracking_mode=tracking_mode,
            )
            debug_log(
                f"[Versioning][Checkpoint] conv={conversation_id} seq={row.get('seq')} "
                f"tracking_mode={tracking_mode} "
                f"msg_index={message_index} snapshot_messages={len(snapshot_messages)} "
                f"tree_hash={row.get('tree_hash')} changed={row.get('changed')} status={row.get('run_status')}"
            )
            cm.update_conversation_metadata(
                conversation_id,
                {
                    "versioning": {
                        "enabled": True,
                        "mode": "overwrite",
                        "tracking_mode": tracking_mode,
                        "backup_mode": "full",
                        "last_commit": row.get("tree_hash"),
                        "last_input_seq": int(row.get("seq") or 0),
                        "updated_at": datetime.now().isoformat(),
                    }
                },
            )
        else:
            # 浅备份：每个用户消息结束时对 tracked files 做快照
            shallow_files: List[Dict[str, Any]] = []
            shallow_insertions = 0
            shallow_deletions = 0
            if message_id:
                shallow_manager = ShallowVersioningManager(
                    project_path=workspace.project_path,
                    data_dir=workspace.data_dir,
                    conversation_id=conversation_id,
                )
                snapshot = shallow_manager.make_snapshot(str(message_id))
                shallow_files = shallow_manager.get_file_diff_stats(str(message_id))
                stats = shallow_manager.get_diff_stats(str(message_id)) or {}
                shallow_insertions = int(stats.get("insertions") or 0)
                shallow_deletions = int(stats.get("deletions") or 0)
                debug_log(
                    f"[Versioning][ShallowSnapshot] conv={conversation_id} "
                    f"msg_id={message_id} tracked_files={snapshot.get('tracked_files_count')}"
                )

            # 同时写入一个轻量 ConversationVersioningManager 检查点行，
            # 让前端版本管理弹窗能统一列出回溯点。
            manager = ConversationVersioningManager(
                project_path=workspace.project_path,
                data_dir=workspace.data_dir,
                conversation_id=conversation_id,
            )
            snapshot_payload = {
                "conversation_id": conversation_id,
                "title": conv_data.get("title"),
                "metadata": conv_data.get("metadata") or {},
                "messages": snapshot_messages,
                "message_index": int(message_index),
                "run_status": str(run_status or "completed"),
                "shallow_message_id": str(message_id) if message_id else None,
            }
            row = manager.create_checkpoint(
                message=message,
                message_index=int(message_index),
                workspace_path=str(workspace.project_path),
                conversation_snapshot=snapshot_payload,
                run_status=str(run_status or "completed"),
                tracking_mode=tracking_mode,
                diff_summary={
                    "insertions": shallow_insertions,
                    "deletions": shallow_deletions,
                    "files_changed": len(shallow_files),
                    "files": shallow_files,
                },
            )
            debug_log(
                f"[Versioning][ShallowCheckpoint] conv={conversation_id} seq={row.get('seq')} "
                f"msg_index={message_index} files={len(shallow_files)} "
                f"insertions={shallow_insertions} deletions={shallow_deletions}"
            )
            cm.update_conversation_metadata(
                conversation_id,
                {
                    "versioning": {
                        "enabled": True,
                        "mode": "overwrite",
                        "tracking_mode": tracking_mode,
                        "backup_mode": "shallow",
                        "last_commit": row.get("tree_hash"),
                        "last_input_seq": int(row.get("seq") or 0),
                        "updated_at": datetime.now().isoformat(),
                    }
                },
            )
    except VersioningError as exc:
        debug_log(f"[Versioning] 记录快照失败: {exc}")
    except Exception as exc:
        debug_log(f"[Versioning] 记录快照异常: {exc}")
    finally:
        # 当前输入处理结束，清除浅备份消息 ID，避免后续无消息上下文时的错误归属。
        try:
            ctx = getattr(web_terminal, "context_manager", None)
            if ctx is not None and hasattr(ctx, "current_shallow_message_id"):
                ctx.current_shallow_message_id = None
        except Exception:
            pass
