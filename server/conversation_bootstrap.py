"""对话进入聚合接口（方案 B：统一加载协议）。

GET /api/conversations/<id>/bootstrap 一次性返回进入对话所需的全部数据：
元数据、文件态消息、运行状态聚合与任务重放决策（needs_rebuild/replay_from）。

只读语义：不写对话文件、不调用 terminal.load_conversation、不切换工作区级 terminal
的当前上下文，天然规避 safe_nav 双轨问题与旧内存回写面。为聚合后台运行状态，
会按需获取/创建对话级 terminal（与既有 running-status 端点行为一致，属内存态操作）。
设计文档：docs/conversation_load_unification_plan.md
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request, session

from server.auth_helpers import api_login_required, get_current_username
from server.context import get_user_resources
from server.tasks import task_manager
from server.tasks.helpers import _task_public_payload
from server.utils_common import debug_log

conversation_bootstrap_bp = Blueprint("conversation_bootstrap", __name__)

_ACTIVE_TASK_STATUSES = {"pending", "running", "cancel_requested"}

# 与前端 compression.ts needsRebuild 判据中的事件类型集合对齐
_ASSISTANT_RESPONSE_EVENTS = {
    "ai_message_start", "thinking_start", "thinking_chunk", "thinking_end",
    "text_start", "text_chunk", "text_end",
    "tool_preparing", "tool_start", "tool_update", "tool_complete",
}
_ASSISTANT_CONTENT_EVENTS = {
    "thinking_chunk", "text_chunk",
    "tool_preparing", "tool_start", "tool_update", "tool_complete",
}


def _normalize_conv_id(conversation_id: str) -> str:
    conv = (conversation_id or "").strip()
    if not conv:
        return conv
    return conv if conv.startswith("conv_") else f"conv_{conv}"


def _find_active_main_task(username: str, normalized_id: str):
    """取该对话最新的活动主任务（对齐 running-status 语义，rec 侧做前缀归一）。"""
    main_rec = None
    for rec in task_manager.list_tasks(username):
        rec_cid = _normalize_conv_id(getattr(rec, "conversation_id", "") or "")
        if rec_cid != normalized_id or rec.status not in _ACTIVE_TASK_STATUSES:
            continue
        if main_rec is None or rec.created_at > main_rec.created_at:
            main_rec = rec
    return main_rec


def _analyze_task_events(events: List[Dict[str, Any]]) -> Dict[str, bool]:
    """分析任务事件流的流式状态（移植自前端 compression.ts needsRebuild 判据）。"""
    in_thinking = False
    in_text = False
    has_text_chunk_event = False
    has_assistant_response_event = False
    has_assistant_content_event = False
    for event in events:
        event_type = str((event or {}).get("type") or "")
        if event_type in _ASSISTANT_RESPONSE_EVENTS:
            has_assistant_response_event = True
        if event_type in _ASSISTANT_CONTENT_EVENTS:
            has_assistant_content_event = True
        if event_type == "thinking_start":
            in_thinking = True
        elif event_type == "thinking_end":
            in_thinking = False
        if event_type == "text_start":
            in_text = True
        elif event_type == "text_end":
            in_text = False
        if event_type == "text_chunk":
            has_text_chunk_event = True
    return {
        "in_thinking": in_thinking,
        "in_text": in_text,
        "has_text_chunk_event": has_text_chunk_event,
        "has_assistant_response_event": has_assistant_response_event,
        "has_assistant_content_event": has_assistant_content_event,
        "force_rebuild_for_streaming_text": in_text or has_text_chunk_event or in_thinking,
    }


def _is_empty_assistant_message(message: Optional[Dict[str, Any]]) -> bool:
    """文件态的「空 assistant」：无正文、无思考内容、无工具调用。

    对应前端判据「末条 assistant 无 actions」——actions 是渲染层结构，
    文件态用内容字段等价判断。
    """
    if not isinstance(message, dict):
        return True
    if str(message.get("content") or "").strip():
        return False
    if str(message.get("reasoning_content") or "").strip():
        return False
    if message.get("tool_calls"):
        return False
    return True


def _decide_task_replay(messages: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """移植前端 needsRebuild 判据：任一成立即 needs_rebuild=True。

    1. 文件态末条不是 assistant（任务产生的 assistant 内容未落盘）；
    2. 文件态末条是空 assistant；
    3. 事件流处于流式中段（inText / hasTextChunkEvent / inThinking）；
    4. 文件态末条 assistant 带 tool_calls（工具结果消息尚未落盘，视为进行中）。
    """
    last_message = messages[-1] if messages else None
    is_assistant = isinstance(last_message, dict) and last_message.get("role") == "assistant"
    event_state = _analyze_task_events(events)
    empty_assistant = _is_empty_assistant_message(last_message) if is_assistant else False
    has_pending_tool_calls = bool(
        is_assistant and isinstance(last_message, dict) and last_message.get("tool_calls")
    )

    needs_rebuild = (
        not is_assistant
        or empty_assistant
        or event_state["force_rebuild_for_streaming_text"]
        or has_pending_tool_calls
    )

    event_count = len(events)
    return {
        "event_count": event_count,
        "needs_rebuild": needs_rebuild,
        "replay_from": 0 if needs_rebuild else event_count,
        "decision_inputs": {
            "is_assistant_last": is_assistant,
            "empty_assistant_last": empty_assistant,
            "has_pending_tool_calls": has_pending_tool_calls,
            **event_state,
        },
    }


@conversation_bootstrap_bp.route("/api/conversations/<conversation_id>/bootstrap", methods=["GET"])
@api_login_required
def bootstrap_conversation(conversation_id: str):
    """进入对话聚合接口：meta + messages + running + task_replay（纯只读）。"""
    username = get_current_username()
    normalized_id = _normalize_conv_id(conversation_id)
    if not normalized_id:
        return jsonify({"success": False, "error": "缺少 conversation_id"}), 400

    workspace_id = (request.args.get("workspace_id") or "").strip() or None

    # 工作区级资源：只取 context_manager 读文件，绝不调用 load_conversation
    try:
        ws_terminal, _workspace = get_user_resources(username, workspace_id=workspace_id)
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 503

    ctx_manager = getattr(ws_terminal, "context_manager", None)
    if ctx_manager is None:
        return jsonify({"success": False, "error": "终端上下文不可用"}), 503

    cm = ctx_manager._get_conversation_manager_for_id(normalized_id)
    conversation_data = cm.load_conversation(normalized_id) if cm else None
    if not conversation_data:
        return jsonify({"success": False, "error": f"对话 {normalized_id} 不存在"}), 404

    raw_meta = conversation_data.get("metadata", {}) or {}
    messages = conversation_data.get("messages", []) or []
    run_mode = raw_meta.get("run_mode") or ("thinking" if raw_meta.get("thinking_mode") else "fast")
    if run_mode == "deep":  # 旧版标识符映射
        run_mode = "thinking"

    # 运行状态聚合（对齐 tasks/api.py running-status：主 task + 三类后台）
    main_rec = _find_active_main_task(username, normalized_id)
    effective_workspace_id = (
        workspace_id
        or (getattr(main_rec, "workspace_id", None) if main_rec else None)
        or (session.get("workspace_id") or None)
    )
    bg_status = {
        "has_running_sub_agents": False,
        "has_running_background_commands": False,
        "has_running_multi_agent": False,
    }
    try:
        conv_terminal, _ = get_user_resources(
            username, workspace_id=effective_workspace_id, conversation_id=normalized_id
        )
    except Exception as exc:
        debug_log(f"[Bootstrap] 获取对话级终端失败: {exc}")
        conv_terminal = None
    if conv_terminal is not None:
        bg_status = task_manager.get_conversation_running_status(conv_terminal, normalized_id)

    is_main_running = main_rec is not None
    running = {
        "is_main_running": is_main_running,
        "main_task_id": getattr(main_rec, "task_id", None) if main_rec else None,
        "main_task_type": getattr(main_rec, "task_type", "chat") if main_rec else None,
        **bg_status,
        "is_truly_active": is_main_running or any(bg_status.values()),
    }

    # 快捷窗口：本次对话编辑/创建过的文件记录（过滤不存在的文件）
    edited_files: list = []
    raw_edited = raw_meta.get("edited_files")
    if isinstance(raw_edited, list):
        file_manager = getattr(ws_terminal, "file_manager", None)
        for item in raw_edited:
            if not isinstance(item, dict):
                continue
            rel_path = str(item.get("path") or "").strip()
            if not rel_path:
                continue
            try:
                valid, _err, full_path = file_manager._validate_path(rel_path) if file_manager else (False, None, None)
            except Exception:
                valid, full_path = False, None
            if valid and full_path is not None and full_path.exists() and full_path.is_file():
                edited_files.append({
                    "path": rel_path,
                    "op": item.get("op") or "edit",
                    "ts": item.get("ts") or "",
                })

    data: Dict[str, Any] = {
        "conversation_id": normalized_id,
        "meta": {
            "title": conversation_data.get("title", "未知对话"),
            "run_mode": run_mode,
            "thinking_mode": bool(raw_meta.get("thinking_mode", run_mode != "fast")),
            "reasoning_effort": raw_meta.get("reasoning_effort"),
            "model_key": raw_meta.get("model_key"),
            "multi_agent_mode": bool(raw_meta.get("multi_agent_mode", False)),
            "permission_mode": raw_meta.get("permission_mode") or "",
            "execution_mode": raw_meta.get("execution_mode") or "",
            "network_permission": raw_meta.get("network_permission") or "",
            "messages_count": len(messages),
        },
        "messages": messages,
        "running": running,
        "edited_files": edited_files,
    }

    if is_main_running:
        events = list(getattr(main_rec, "events", []) or [])
        replay = _decide_task_replay(messages, events)
        replay["task_id"] = main_rec.task_id
        # 任务摘要与全量事件：前端恢复时免去 GET /api/tasks 与 GET /api/tasks/{id} 两次请求
        replay["task"] = _task_public_payload(main_rec)
        replay["events"] = events
        data["task_replay"] = replay
        debug_log(
            f"[Bootstrap] conv={normalized_id} task={main_rec.task_id} "
            f"events={replay['event_count']} needs_rebuild={replay['needs_rebuild']} "
            f"inputs={replay['decision_inputs']}"
        )

    return jsonify({"success": True, "data": data})
