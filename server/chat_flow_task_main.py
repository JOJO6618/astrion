from __future__ import annotations
import asyncio
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional
from config import AUTO_FIX_TOOL_CALL, AUTO_FIX_MAX_ATTEMPTS, MAX_ITERATIONS_PER_TASK, MAX_CONSECUTIVE_SAME_TOOL, MAX_TOTAL_TOOL_CALLS
from modules.personalization_manager import load_personalization_config, resolve_context_compression_settings
from modules.skill_hint_manager import SkillHintManager
from modules.i18n import tr
from modules.user_manager import UserWorkspace
from modules.multi_agent.debug_logger import ma_debug
from core.web_terminal import WebTerminal
from config.model_profiles import get_default_model_key, get_model_context_window, get_model_profile
from .work_timer import finalize_conversation_work_timer
from .utils_common import debug_log
from .extensions import run_background
from .state import get_stop_flag, clear_stop_flag
from .chat_flow_runtime import generate_conversation_title_background, detect_malformed_tool_call
from .chat_flow_task_support import process_background_command_updates, process_multi_agent_master_messages, inject_runtime_user_message, _auto_message_type_for_multi_agent_subtype
from .chat_flow_tool_loop import execute_tool_calls
from .chat_flow_stream_loop import run_streaming_attempts
from .deep_compression import run_deep_compression
from .goal_flow import inject_goal_prompt, handle_goal_after_turn, stop_goal_user_cancel, goal_is_active, emit_goal_progress
from server.attachment_paths import build_media_paths_message as _build_media_paths_message, build_file_paths_message as _build_file_paths_message, normalize_attached_files as _normalize_attached_files
from .chat_flow_message_policy import _VALID_USER_MESSAGE_SOURCES
from .chat_flow_notice_state import _build_shared_waiting_payload
from .chat_flow_notice_state import _has_pending_completion_work
from .chat_flow_notice_state import _has_pending_workflow_notices
from .chat_flow_versioning import _prepare_hidden_versioning_baseline_for_first_input
from .chat_flow_versioning import _record_hidden_versioning_checkpoint_after_run
from .chat_flow_message_policy import _user_message_ui_defaults
from .chat_flow_notice_poll import poll_completion_notifications
from .chat_flow_notice_poll import poll_multi_agent_notifications


from .chat_flow_message_policy import _user_message_ui_defaults
from .chat_flow_versioning import _should_skip_versioning_for_message, _prepare_hidden_versioning_baseline_for_first_input, _record_hidden_versioning_checkpoint_after_run
from .chat_flow_notice_dispatch import _persist_and_echo_preceding_notice, _dispatch_completion_user_notice
from .chat_flow_notice_state import _build_shared_waiting_payload, _build_sub_agent_notice_text, _collect_pending_completion_notices, _rollback_completion_notice_marks, _has_pending_completion_work, _has_pending_workflow_notices
from .chat_flow_notice_poll import poll_completion_notifications, poll_multi_agent_notifications
from .chat_flow_notice_idle import _dispatch_multi_agent_idle_messages, _dispatch_multi_agent_idle_messages_sync


async def handle_task_with_sender(
    terminal: WebTerminal,
    workspace: UserWorkspace,
    message,
    images,
    sender,
    client_sid,
    username: str,
    videos=None,
    auto_user_message_event: bool = False,
    files=None,
    work_anchor_message_id: Optional[str] = None,
):
    """处理任务并发送消息 - 集成token统计版本"""

    web_terminal = terminal
    is_agent = getattr(terminal, "conversation_kind", "agent") == "agent"
    conversation_id = getattr(web_terminal.context_manager, "current_conversation_id", None)
    # 多智能体模式：标记主对话任务正在运行，供后台通知池判断是否可以安全消费
    if getattr(web_terminal, "sub_agent_manager", None) is not None:
        web_terminal._multi_agent_main_task_active = True
        manager = getattr(web_terminal, "sub_agent_manager", None)
        pending_count = 0
        if manager:
            state = manager.get_multi_agent_state(conversation_id)
            if state:
                pending_count = len(state.pending_master_messages)
        ma_debug(
            "handle_task_with_sender_start",
            conversation_id=conversation_id,
            terminal_id=id(web_terminal),
            manager_id=id(manager) if manager else None,
            message_preview=str(message)[:300] if message else None,
            pending_master_messages_count=pending_count,
        )
    videos = videos or []
    files = _normalize_attached_files(files)
    raw_sender = sender

    def sender(event_type, data):
        """为关键事件补充会话标识，便于前端定位报错归属。"""
        if not isinstance(data, dict):
            raw_sender(event_type, data)
            return
        payload = dict(data)
        current_conv = conversation_id or getattr(web_terminal.context_manager, "current_conversation_id", None)
        if event_type in {"task_stopped", "quota_exceeded"}:
            summary_work = getattr(web_terminal, "_thinking_summary_work", None)
            if summary_work:
                summary_work.cancel()

        # 为所有事件添加 conversation_id，确保前端能正确匹配
        if current_conv and event_type not in {"connect", "disconnect", "system_ready"}:
            payload.setdefault("conversation_id", current_conv)

        # 调试信息：记录关键事件
        if event_type in {"user_message", "ai_message_start", "text_start", "text_chunk", "tool_preparing"}:
            debug_log(f"[SENDER] 发送事件: {event_type}, conversation_id={current_conv}, data_keys={list(payload.keys())}")

        # 为关键事件添加额外的标识信息
        if event_type in {"error", "quota_exceeded", "task_stopped", "task_complete"}:
            task_id = getattr(web_terminal, "task_id", None) or client_sid
            if task_id:
                payload.setdefault("task_id", task_id)
            if client_sid:
                payload.setdefault("client_sid", client_sid)

        raw_sender(event_type, payload)
    
    # 添加到对话历史
    user_work_started_at = datetime.now().isoformat()
    user_message_index = -1
    current_work_user_message_index = -1
    finalized_work_message_indices: set[int] = set()
    history_len_before = len(getattr(web_terminal.context_manager, "conversation_history", []) or [])
    is_first_user_message = history_len_before == 0
    if not is_agent:
        # Chat 在受理时已经保存首条 user；后续分支、重生成和压缩续接不再起标题。
        turns = (web_terminal.context_manager.conversation_metadata or {}).get("chat", {}).get("turns", [])
        is_first_user_message = (
            history_len_before == 1 and len(turns) == 1
            and turns[0].get("id") == getattr(web_terminal, "turn_id", None)
        )
    # 构建 user 消息来源与 metadata
    source_from_terminal = str(getattr(web_terminal, "_current_user_message_source", "user") or "user").strip().lower()
    user_message_source = source_from_terminal or "user"
    multi_agent_meta: Dict[str, Any] = {}
    if auto_user_message_event:
        auto_payload = getattr(web_terminal, "_auto_user_message_payload", None)
        if isinstance(auto_payload, dict):
            src = str(auto_payload.get("message_source") or "").strip().lower()
            if src:
                user_message_source = src
            elif auto_payload.get("runtime_mode_notice"):
                user_message_source = "notify"
            elif auto_payload.get("runtime_guidance"):
                user_message_source = "guidance"
            elif auto_payload.get("background_command_notice"):
                user_message_source = "background_command"
            elif auto_payload.get("sub_agent_notice"):
                user_message_source = "sub_agent"
            # 保留多智能体元数据，避免前端把子智能体输出渲染成普通 user 消息
            if auto_payload.get("multi_agent_message"):
                multi_agent_meta["multi_agent_message"] = True
                multi_agent_meta["multi_agent_display_name"] = auto_payload.get("multi_agent_display_name")
                multi_agent_meta["multi_agent_subtype"] = auto_payload.get("multi_agent_subtype")
    if user_message_source not in _VALID_USER_MESSAGE_SOURCES:
        user_message_source = "user"

    # 构建user消息metadata
    user_message_metadata = {
        "message_source": user_message_source,
    }
    user_message_metadata.update(
        _user_message_ui_defaults(
            user_message_source,
            auto_user_message_event=bool(auto_user_message_event),
        )
    )
    if user_message_metadata.get("starts_work") is True:
        user_message_metadata["work_timer"] = {
            "status": "working",
            "started_at": user_work_started_at
        }
    # 如果是自动发送的user消息（子智能体/后台命令完成通知），添加标记
    if auto_user_message_event:
        user_message_metadata["is_auto_generated"] = True
        if multi_agent_meta.get("multi_agent_message"):
            # 多智能体消息：恢复专用 auto_message_type 与显示字段
            user_message_metadata["auto_message_type"] = _auto_message_type_for_multi_agent_subtype(
                multi_agent_meta.get("multi_agent_subtype")
            )
            user_message_metadata.update(multi_agent_meta)
            # 多智能体消息是正常聊天消息，走多智能体动态专用渲染，
            # 不应走 _user_message_ui_defaults 默认给出的 visibility="compact"（通知渲染）。
            user_message_metadata["visibility"] = "chat"
            # 多智能体消息触发的是主智能体对子智能体输出的续答，不应显示新的
            # assistant 回复头部（Astrion/工作时间），因此 starts_work=false。
            # 前端通过单独的 multi_agent_message 标记恢复轮询。
            user_message_metadata["starts_work"] = False
        else:
            user_message_metadata["auto_message_type"] = "completion_notice"
    # 附加文件路径记录在主消息 metadata 中：前端据此在气泡内渲染文件块，历史恢复同理
    if files:
        user_message_metadata["files"] = list(files)
    saved_user_message = web_terminal.context_manager.add_conversation(
        "user",
        message,
        images=images,
        videos=videos,
        metadata=user_message_metadata,
        message_id=getattr(web_terminal, "_display_trigger_message_id", None),
    )
    web_terminal._display_trigger_message_id = None
    try:
        user_message_index = len(getattr(web_terminal.context_manager, "conversation_history", []) or []) - 1
    except Exception:
        user_message_index = -1
    current_work_user_message_index = user_message_index

    # 本段工作的锚点消息：编辑摘要、工作计时与 tokens/s 都归属这一条。
    current_work_anchor_message_id = (
        saved_user_message.get("message_id") if isinstance(saved_user_message, dict) else None
    )
    # 压缩续接：压缩前 + 压缩后是同一工作段，锚点沿用上一段（不回落到引导语消息）。
    # 否则编辑摘要会挂到 starts_work=false 的引导语上，段真正结束时也无人给原消息收尾。
    inherited_anchor_id = str(work_anchor_message_id or "").strip()
    inherited_anchor_index = -1
    if inherited_anchor_id:
        history_now = getattr(web_terminal.context_manager, "conversation_history", None) or []
        for idx in range(len(history_now) - 1, -1, -1):
            item = history_now[idx] or {}
            if item.get("role") == "user" and item.get("message_id") == inherited_anchor_id:
                inherited_anchor_index = idx
                break
        if inherited_anchor_index >= 0:
            current_work_anchor_message_id = inherited_anchor_id
            current_work_user_message_index = inherited_anchor_index
    from modules.thinking_summary import begin_work
    begin_work(web_terminal, saved_user_message or message, inherited_anchor_id)

    # 为浅备份 track_edit 提供当前用户消息 ID，避免文件修改被归到上一个输入。
    try:
        web_terminal.context_manager.current_shallow_message_id = current_work_anchor_message_id
    except Exception:
        web_terminal.context_manager.current_shallow_message_id = None
    # tokens/s 样本按工作段累积：只有真正开启工作的新消息才清空（通知类任务不清），
    # 压缩续接沿用同段样本。
    if inherited_anchor_index < 0 and user_message_metadata.get("starts_work") is True:
        try:
            web_terminal.context_manager.reset_work_segment_speed_samples()
        except Exception:
            pass

    skill_context_messages = getattr(web_terminal, "_skill_context_messages", None)
    if not auto_user_message_event and isinstance(skill_context_messages, list):
        for skill_item in skill_context_messages:
            if not isinstance(skill_item, dict):
                continue
            skill_content = str(skill_item.get("content") or "")
            if not skill_content:
                continue
            skill_name = str(skill_item.get("name") or "").strip()
            skill_path = str(skill_item.get("path") or "").strip()
            web_terminal.context_manager.add_conversation(
                "user",
                f"[系统通知|skill]\n{skill_content}",
                metadata={
                    "source": "skill",
                    "message_source": "skill",
                    "visibility": "hidden",
                    "starts_work": False,
                    "hidden": True,
                    "skill_name": skill_name,
                    "skill_path": skill_path,
                }
            )
    if is_agent and not auto_user_message_event:
        media_paths_message = _build_media_paths_message(images, videos)
        if media_paths_message:
            web_terminal.context_manager.add_conversation(
                "user",
                media_paths_message,
                metadata={
                    "source": "media_paths",
                    "message_source": "media_paths",
                    "visibility": "hidden",
                    "starts_work": False,
                    "hidden": True,
                }
            )
        file_paths_message = _build_file_paths_message(files)
        if file_paths_message:
            web_terminal.context_manager.add_conversation(
                "user",
                file_paths_message,
                metadata={
                    "source": "file_paths",
                    "message_source": "file_paths",
                    "visibility": "hidden",
                    "starts_work": False,
                    "hidden": True,
                }
            )
        try:
            sender(
                'user_message',
                {
                    "message": message,
                    "message_id": (saved_user_message or {}).get("message_id"),
                    "is_task_input": True,
                    "images": (saved_user_message or {}).get("images") or images or [],
                    "videos": (saved_user_message or {}).get("videos") or videos or [],
                    "media_refs": (saved_user_message or {}).get("media_refs") or [],
                    "message_source": user_message_source,
                    "visibility": user_message_metadata.get("visibility"),
                    "starts_work": user_message_metadata.get("starts_work"),
                    "metadata": user_message_metadata,
                    "conversation_id": conversation_id,
                    "timestamp": (saved_user_message or {}).get("timestamp") or user_work_started_at,
                },
            )
        except Exception as exc:
            debug_log(f"[TaskFlow] 发送 user_message 回显失败: {exc}")
        # 空闲期运行期模式（权限/执行环境/网络权限）切换的补发通知。
        # 仅真实用户消息（直接发送 / 提前输入队列 / 引导消息转换）触发，
        # 自动消息（子智能体/后台命令完成等触发的续答）不触发。
        # drift 检测比较「当前实际模式 vs 本对话已通知基线」，天然去重：
        # 空闲期反复切换只在最终值与基线不同时补发一遍；
        # 基线缺失（新对话/历史对话）时静默初始化，不补发。
        if user_message_source in {"user", "presend", "guidance"}:
            try:
                drift = (
                    web_terminal.collect_runtime_mode_drift()
                    if hasattr(web_terminal, "collect_runtime_mode_drift")
                    else []
                )
            except Exception as exc:
                drift = []
                debug_log(f"[RuntimeMode] 空闲期模式漂移检测失败: {exc}")
            for drift_item in drift:
                try:
                    notice_text = web_terminal.build_runtime_mode_switch_notice(
                        drift_item.get("kind"), drift_item.get("mode")
                    )
                    if not str(notice_text or "").strip():
                        continue
                    inject_runtime_user_message(
                        web_terminal=web_terminal,
                        messages=None,
                        text=notice_text,
                        source=str(drift_item.get("source") or "notify"),
                        sender=sender,
                        conversation_id=conversation_id,
                        inline=False,
                    )
                    web_terminal.update_runtime_mode_baseline(
                        {drift_item.get("kind"): drift_item.get("mode")}
                    )
                except Exception as exc:
                    debug_log(f"[RuntimeMode] 空闲期切换通知注入失败: {exc}")
    if is_agent:
        _prepare_hidden_versioning_baseline_for_first_input(
            web_terminal=web_terminal,
            workspace=workspace,
            conversation_id=conversation_id,
            message=message,
            auto_user_message_event=bool(auto_user_message_event),
        )

    def finalize_user_work_timer(index: Optional[int] = None):
        target_index = current_work_user_message_index if index is None else index
        if target_index in finalized_work_message_indices:
            return
        history = getattr(web_terminal.context_manager, "conversation_history", None) or []
        if target_index < 0 or target_index >= len(history):
            return
        target_msg = history[target_index] or {}
        if target_msg.get("role") != "user":
            return
        metadata = target_msg.get("metadata") or {}
        timer = metadata.get("work_timer")
        if not isinstance(timer, dict):
            return
        started_at = timer.get("started_at") or target_msg.get("timestamp") or user_work_started_at
        start_ts = None
        try:
            start_ts = datetime.fromisoformat(str(started_at).replace("Z", "+00:00")).timestamp()
        except Exception:
            start_ts = None
        now_ts = time.time()
        duration_ms = int(max(0.0, (now_ts - start_ts) * 1000.0)) if start_ts is not None else 0
        # 本段 tokens/s 加权平均：工作段真正结束时写入锚点消息（与 work_timer 同一条）。
        # 压缩续接不在此收尾，因此压缩前 + 压缩后归为同一段统计。
        try:
            speed_summary = web_terminal.context_manager.consume_work_segment_speed_summary()
        except Exception as exc:
            speed_summary = None
            debug_log(f"[TokenSpeed] 段速度汇总失败: {exc}")
        if speed_summary:
            metadata["token_speed"] = speed_summary
            # 同一事件即时下发：前端任务完成时只本地标记 work_timer、不重载历史，
            # 速度行必须靠该字段就地渲染（刷新后则由消息 metadata 恢复）。
            try:
                web_terminal.context_manager.broadcast_work_segment_speed(
                    target_msg.get("message_id"), speed_summary
                )
            except Exception:
                pass
        timer.update({
            "status": "completed",
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(),
            "duration_ms": duration_ms
        })
        metadata["work_timer"] = timer
        target_msg["metadata"] = metadata
        history[target_index] = target_msg
        web_terminal.context_manager.auto_save_conversation(force=True)
        finalized_work_message_indices.add(target_index)
        if not is_agent:
            return
        # 修改留痕收尾：写入完成时间；被 rm/移动的文件转为可恢复 diff（实时内容已在编辑时落盘）
        try:
            from modules.modify_history import finalize_modify_history_for_task
            finalize_modify_history_for_task(
                project_path=getattr(web_terminal, "project_path", None),
                data_dir=getattr(web_terminal, "data_dir", None),
                conversation_id=conversation_id,
                msg=target_msg,
                finished_at=timer.get("finished_at"),
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(f"[modify_history] 收尾钩子执行失败: {exc}")
        finalized_work_message_indices.add(target_index)

    def switch_current_work_timer_to_latest_user():
        nonlocal current_work_user_message_index
        history = getattr(web_terminal.context_manager, "conversation_history", None) or []
        for idx in range(len(history) - 1, -1, -1):
            msg = history[idx] or {}
            if msg.get("role") == "user":
                metadata = msg.get("metadata") or {}
                if isinstance(metadata.get("work_timer"), dict):
                    current_work_user_message_index = idx
                    return

    versioning_checkpoint_recorded = False

    def finalize_run_versioning_checkpoint(run_status: str = "completed"):
        nonlocal versioning_checkpoint_recorded
        if versioning_checkpoint_recorded or not is_agent:
            return
        _record_hidden_versioning_checkpoint_after_run(
            web_terminal=web_terminal,
            workspace=workspace,
            conversation_id=conversation_id,
            message=message,
            message_index=user_message_index,
            auto_user_message_event=bool(auto_user_message_event),
            run_status=run_status,
        )
        versioning_checkpoint_recorded = True

    # Skill 提示系统：检测关键词并在用户消息之后插入 system 消息
    try:
        personal_config = load_personalization_config(workspace.data_dir) if is_agent else {}
        skill_hints_enabled = personal_config.get("skill_hints_enabled", False)

        if skill_hints_enabled and message:
            hint_manager = SkillHintManager()
            hint_manager.set_enabled(True)
            hint_messages = hint_manager.build_hint_messages(message)

            # 将提示消息插入到对话历史中（在用户消息之后）
            for hint_msg in hint_messages:
                debug_log(f"[Skill Hints] 插入提示消息: {hint_msg['content'][:100]}")
                web_terminal.context_manager.add_conversation(
                    "system",
                    hint_msg["content"]
                )
                # 验证插入后的消息
                last_msg = web_terminal.context_manager.conversation_history[-1]
                debug_log(f"[Skill Hints] 插入后验证 - role: {last_msg.get('role')}, content: {last_msg.get('content')[:100]}")
    except Exception as exc:
        debug_log(f"Skill hints 处理失败: {exc}")

    if is_first_user_message and getattr(web_terminal, "context_manager", None):
        try:
            preferences_dir = workspace.data_dir if is_agent else web_terminal.preferences_dir
            personal_config = load_personalization_config(preferences_dir)
        except Exception:
            personal_config = {}
        auto_title_enabled = personal_config.get("auto_generate_title", True)
        skip_auto_title_generation = bool(
            (web_terminal.context_manager.conversation_metadata or {}).get("skip_auto_title_generation", False)
        )
        if auto_title_enabled and not skip_auto_title_generation:
            conv_id = getattr(web_terminal.context_manager, "current_conversation_id", None)
            run_background(
                generate_conversation_title_background,
                web_terminal,
                conv_id,
                message,
                username,
                personal_config.get("title_model", "")
            )

    # Agent 的文件式压缩不适用于 Chat；Chat 在请求钩子中压缩分支投影。
    try:
        personal_config = load_personalization_config(workspace.data_dir) if is_agent else {}
    except Exception:
        personal_config = {}
    compression_settings = resolve_context_compression_settings(personal_config)
    auto_deep_enabled = bool(personal_config.get("auto_deep_compress_enabled", False))
    current_tokens_for_deep = web_terminal.context_manager.get_current_context_tokens(conversation_id)
    if (
        auto_deep_enabled
        and current_tokens_for_deep > compression_settings["deep_trigger_tokens"]
        and not web_terminal.context_manager.is_compression_in_progress()
    ):
        web_terminal.context_manager._set_meta_flag("is_ultra_long_conversation", True)
        sender('compression_state', {
            "conversation_id": conversation_id,
            "in_progress": True,
            "mode": "auto",
            "stage": "queued"
        })
        deep_result = await run_deep_compression(
            web_terminal=web_terminal,
            workspace=workspace,
            conversation_id=conversation_id,
            mode="auto",
            sender=sender,
        )
        if not deep_result.get("success"):
            sender('error', {
                "message": deep_result.get("error") or tr("tool_loop.deep_compression_failed"),
                "conversation_id": conversation_id,
            })
        else:
            guide_message = (deep_result.get("guide_message") or "").strip()
            if guide_message:
                # 压缩续接视为同一工作段：不在压缩处收尾 work_timer，
                # 锚点（编辑摘要/计时/tokens）由续接任务继承。
                finalize_run_versioning_checkpoint("auto_deep_compress_handoff")
                # 压缩后的续接引导语单独标记来源，使其以 compact 形式展示，
                # 而不是混入普通用户消息流。
                setattr(web_terminal, "_current_user_message_source", "compression_handoff")
                try:
                    await handle_task_with_sender(
                        web_terminal,
                        workspace,
                        guide_message,
                        [],
                        sender,
                        client_sid,
                        username,
                        [],
                        work_anchor_message_id=current_work_anchor_message_id,
                    )
                finally:
                    setattr(web_terminal, "_current_user_message_source", "user")
                return
        finalize_user_work_timer()
        finalize_run_versioning_checkpoint("auto_deep_compress_end")
        return
    
    # === 移除：不在这里计算输入token，改为在每次API调用前计算 ===
    
    # 构建上下文和消息（用于API调用）
    context = web_terminal.build_context()
    messages = web_terminal.build_messages(context, message)
    tools = web_terminal.define_tools()

    # === 工作流：真实用户消息到达时清零单步轮数（知情交互，撞限询问后可重新计数）===
    if is_agent and user_message_source == "user":
        try:
            from modules.workflow_state_manager import WorkflowStateManager as _WorkflowStateManager
            _wf_mgr = _WorkflowStateManager(web_terminal.data_dir, conversation_id)
            if _wf_mgr.is_active():
                _wf_mgr.reset_stage_rounds()
        except Exception:
            pass

    # === 工作流：激活中的对话在任务开头广播一次进度快照进事件流。
    # REST activate 的 socketio 广播在轮询架构下到不了前端；事件流才是可靠通道。
    try:
        from server.workflow_flow import get_active_manager as _get_active_wfm, emit_workflow_progress as _emit_wf_progress
        _wsm_boot = _get_active_wfm(web_terminal.data_dir, conversation_id) if is_agent else None
        if _wsm_boot is not None:
            _emit_wf_progress(wsm=_wsm_boot, sender=sender, conversation_id=conversation_id)
    except Exception as _wf_boot_exc:
        debug_log(f"[Workflow] 任务开头进度广播失败: {_wf_boot_exc}")

    # Admission owns start/resume. Compression only re-injects the same goal.
    try:
        if goal_is_active(workspace, conversation_id):
            inject_goal_prompt(
                web_terminal=web_terminal,
                messages=messages,
                sender=sender,
                conversation_id=conversation_id,
            )
            emit_goal_progress(
                web_terminal=web_terminal,
                workspace=workspace,
                sender=sender,
                conversation_id=conversation_id,
                total_tool_calls=0,
            )
    except Exception as exc:
        debug_log(f"[Goal] 启动/注入失败: {exc}")

    try:
        profile = get_model_profile(getattr(web_terminal, "model_key", None))
        web_terminal.apply_model_profile(profile)
    except Exception as exc:
        debug_log(f"更新模型配置失败: {exc}")

    # === 空模型注册表守卫：终端未持有模型（全新部署/未配置提供商，构造时
    # 注册表为空置 None）时，发消息应得到干净的引导错误并终止任务，而不是让
    # 后续 get_model_context_window 抛未处理 ValueError 变成任务级 500。
    # 若终端构造后用户已新配模型（注册表非空），这里顺手补上默认模型继续任务。
    if not getattr(web_terminal, "model_key", None):
        try:
            resolved_key = get_default_model_key()
            resolved_profile = get_model_profile(resolved_key)
        except ValueError as exc:
            err_msg = str(exc)
            web_terminal.context_manager.add_conversation("system", err_msg)
            sender('error', {
                'message': err_msg,
                'status_code': 400,
                'error_type': 'no_model_configured'
            })
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("no_model_configured")
            return
        web_terminal.model_key = resolved_key
        web_terminal.model_profile = resolved_profile
        web_terminal.apply_model_profile(resolved_profile)

    # === 上下文预算与安全校验（避免超出模型上下文） ===
    max_context_tokens = get_model_context_window(getattr(web_terminal, "model_key", None))
    current_tokens = web_terminal.context_manager.get_current_context_tokens(conversation_id)
    # 提前同步给底层客户端，动态收缩 max_tokens
    web_terminal.api_client.update_context_budget(current_tokens, max_context_tokens)
    if max_context_tokens:
        if current_tokens >= max_context_tokens:
            err_msg = tr(
                "task_main.context_overflow",
                current=current_tokens,
                max=max_context_tokens,
            )
            debug_log(err_msg)
            web_terminal.context_manager.add_conversation("system", err_msg)
            sender('error', {
                'message': err_msg,
                'status_code': 400,
                'error_type': 'context_overflow'
            })
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("context_overflow")
            return
        usage_percent = (current_tokens / max_context_tokens) * 100
        warned = web_terminal.context_manager.conversation_metadata.get("context_warning_sent", False)
        if usage_percent >= 70 and not warned:
            warn_msg = tr(
                "task_main.context_usage_hint",
                percent=f"{usage_percent:.1f}",
                current=current_tokens,
                max=max_context_tokens,
            )
            web_terminal.context_manager.conversation_metadata["context_warning_sent"] = True
            web_terminal.context_manager.auto_save_conversation(force=True)
            sender('context_warning', {
                'title': tr("task_main.context_overflow_title"),
                'message': warn_msg,
                'type': 'warning',
                'conversation_id': conversation_id
            })
    
    # 开始新的AI消息
    sender('ai_message_start', {})
    
    # 增量保存相关变量
    accumulated_response = ""   # 累积的响应内容
    is_first_iteration = True   # 是否是第一次迭代
    
    # 统计和限制变量
    total_iterations = 0
    total_tool_calls = 0
    # 行内引用：任务级已用来源累积（task_complete 时带给前端实时渲染），按 id 去重
    task_citations: Dict[str, dict] = {}
    consecutive_same_tool = defaultdict(int)
    last_tool_name = ""
    auto_fix_attempts = 0
    last_tool_call_time = 0
    detected_tool_intent: Dict[str, str] = {}
    # 目标模式：记录本“目标轮段”内主模型是否调用过工具（空转保护用）。
    # 每次续命注入后重置为 False，直到下一次 no-tool-call break。
    goal_segment_made_tool_call = False
    
    # 设置最大迭代次数（API 可覆盖）；None 表示不限制
    max_iterations_override = getattr(web_terminal, "max_iterations_override", None)
    max_iterations = max_iterations_override if max_iterations_override is not None else MAX_ITERATIONS_PER_TASK
    max_api_retries = 4
    retry_delay_seconds = 10
    

    async def continue_after_compression(deep_result):
        guide_message = (deep_result.get("guide_message") or "").strip()
        if deep_result.get("success") and guide_message:
            # 压缩续接仍属于当前工作段，保留编辑摘要、计时及速度锚点。
            finalize_run_versioning_checkpoint("deep_compress_handoff")
            setattr(web_terminal, "_current_user_message_source", "compression_handoff")
            try:
                await handle_task_with_sender(
                    web_terminal, workspace, guide_message, [], sender, client_sid,
                    username, [], work_anchor_message_id=current_work_anchor_message_id,
                )
            finally:
                setattr(web_terminal, "_current_user_message_source", "user")
            return
        finalize_user_work_timer()
        finalize_run_versioning_checkpoint("deep_compress_end")

    from server.tasks.compression_requests import run_requested_compression

    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        # 检查停止标志
        stop_entry = get_stop_flag(client_sid, username, include_user=False)
        if stop_entry and stop_entry.get('stop'):
            debug_log(f"[Task] 检测到停止标志，退出循环")
            try:
                stop_goal_user_cancel(
                    web_terminal=web_terminal,
                    workspace=workspace,
                    sender=sender,
                    conversation_id=conversation_id,
                    total_tool_calls=total_tool_calls,
                )
            except Exception as exc:
                debug_log(f"[Goal] 用户停止时停止目标失败: {exc}")
            # 计算后台任务状态，前端用这个字段决定是否保持对话运行态、输入区是否释放
            _stopped_has_bg = False
            _stopped_has_bg_cmd = False
            _stopped_has_ma = False
            _manager = getattr(web_terminal, "sub_agent_manager", None)
            if _manager:
                try:
                    _manager.reconcile_task_states(conversation_id=conversation_id)
                except Exception:
                    pass
                if _manager:
                    _state = _manager.get_multi_agent_state(conversation_id)
                    if _state:
                        _stopped_has_ma = any(a.status == "running" for a in _state.list_all())
            _bg_mgr = getattr(web_terminal, "background_command_manager", None)
            if _bg_mgr and conversation_id:
                try:
                    _bg_mgr.reconcile_stale_records(conversation_id=conversation_id)
                except Exception:
                    pass
                if _bg_mgr.list_waiting_items(conversation_id):
                    _stopped_has_bg_cmd = True
            sender('task_stopped', {
                'message': tr("task_main.task_stopped"),
                'reason': 'user_requested',
                'has_running_sub_agents': _stopped_has_bg,
                'has_running_background_commands': _stopped_has_bg_cmd,
                'has_running_multi_agent': _stopped_has_ma,
            })
            break

        current_iteration = iteration + 1
        iteration += 1
        total_iterations += 1
        iteration_limit_label = max_iterations if max_iterations is not None else "∞"
        debug_log(f"\n--- 迭代 {current_iteration}/{iteration_limit_label} 开始 ---")

        # === 工作流：单步轮数计数与撞限柔性通知 ===
        try:
            from modules.workflow_state_manager import WorkflowStateManager as _WorkflowStateManager2
            from server.workflow_flow import build_round_limit_notice as _build_round_limit_notice
            _wf_mgr2 = _WorkflowStateManager2(web_terminal.data_dir, conversation_id) if is_agent else None
            if _wf_mgr2 is not None and _wf_mgr2.is_active():
                _wf_mgr2.increment_stage_rounds()
                _wf_notice = _build_round_limit_notice(
                    data_dir=web_terminal.data_dir,
                    conversation_id=conversation_id,
                )
                if _wf_notice:
                    if goal_is_active(workspace, conversation_id):
                        _wf_notice = "工作流已达到阶段轮数限制。目标模式下不得等待人工决定；请在现有权限内调整方案，无法推进时说明阻塞原因。"
                    web_terminal.inject_runtime_user_message(
                        _wf_notice,
                        messages=messages,
                        source="workflow",
                        inline=True,
                    )
        except Exception as _wf_exc:
            debug_log(f"[Workflow] 轮数计数/撞限通知失败: {_wf_exc}")
        
        # 检查是否超过总工具调用限制
        if MAX_TOTAL_TOOL_CALLS is not None and total_tool_calls >= MAX_TOTAL_TOOL_CALLS:
            debug_log(f"已达到最大工具调用次数限制 ({MAX_TOTAL_TOOL_CALLS})")
            sender('system_message', {
                'content': tr("task_main.max_tool_calls_reached", limit=MAX_TOTAL_TOOL_CALLS)
            })
            break
        
        full_response = ""
        tool_calls = []
        current_thinking = ""
        detected_tools = {}
        last_usage_payload = None
        
        # 状态标志
        in_thinking = False
        thinking_started = False
        thinking_ended = False
        text_started = False
        text_has_content = False
        text_streaming = False
        text_chunk_index = 0
        last_text_chunk_time: Optional[float] = None
        
        # 计数器
        chunk_count = 0
        reasoning_chunks = 0
        content_chunks = 0
        tool_chunks = 0
        last_finish_reason = None

        thinking_expected = web_terminal.api_client.get_current_thinking_mode()
        debug_log(f"思考模式: {thinking_expected}")
        quota_allowed = True
        quota_info = {}
        if hasattr(web_terminal, "record_model_call"):
            quota_allowed, quota_info = web_terminal.record_model_call(bool(thinking_expected))
        if not quota_allowed:
            quota_type = 'thinking' if thinking_expected else 'fast'
            sender('quota_exceeded', {
                'type': quota_type,
                'reset_at': quota_info.get('reset_at')
            })
            sender('error', {
                'message': tr("task_main.quota_exceeded"),
                'quota': quota_info
            })
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("quota_exceeded")
            return
        
        tool_call_limit_label = MAX_TOTAL_TOOL_CALLS if MAX_TOTAL_TOOL_CALLS is not None else "∞"
        debug_log(f"[API] 第{current_iteration}次调用 (总工具调用: {total_tool_calls}/{tool_call_limit_label})")
        
        prepare_request = getattr(web_terminal, "prepare_model_messages", None)
        if callable(prepare_request):
            messages = await prepare_request(messages, sender=sender)
        stream_result = await run_streaming_attempts(
            web_terminal=web_terminal,
            messages=messages,
            tools=tools,
            sender=sender,
            client_sid=client_sid,
            username=username,
            conversation_id=conversation_id,
            current_iteration=current_iteration,
            max_api_retries=max_api_retries,
            retry_delay_seconds=retry_delay_seconds,
            detected_tool_intent=detected_tool_intent,
            full_response=full_response,
            tool_calls=tool_calls,
            current_thinking=current_thinking,
            detected_tools=detected_tools,
            last_usage_payload=last_usage_payload,
            in_thinking=in_thinking,
            thinking_started=thinking_started,
            thinking_ended=thinking_ended,
            text_started=text_started,
            text_has_content=text_has_content,
            text_streaming=text_streaming,
            text_chunk_index=text_chunk_index,
            last_text_chunk_time=last_text_chunk_time,
            chunk_count=chunk_count,
            reasoning_chunks=reasoning_chunks,
            content_chunks=content_chunks,
            tool_chunks=tool_chunks,
            last_finish_reason=last_finish_reason,
            accumulated_response=accumulated_response,
        )
        if stream_result.get("stopped"):
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("stopped")
            return

        full_response = stream_result["full_response"]
        tool_calls = stream_result["tool_calls"]
        current_thinking = stream_result["current_thinking"]
        detected_tools = stream_result["detected_tools"]
        last_usage_payload = stream_result["last_usage_payload"]
        in_thinking = stream_result["in_thinking"]
        thinking_started = stream_result["thinking_started"]
        thinking_ended = stream_result["thinking_ended"]
        text_started = stream_result["text_started"]
        text_has_content = stream_result["text_has_content"]
        text_streaming = stream_result["text_streaming"]
        text_chunk_index = stream_result["text_chunk_index"]
        last_text_chunk_time = stream_result["last_text_chunk_time"]
        chunk_count = stream_result["chunk_count"]
        reasoning_chunks = stream_result["reasoning_chunks"]
        content_chunks = stream_result["content_chunks"]
        tool_chunks = stream_result["tool_chunks"]
        last_finish_reason = stream_result["last_finish_reason"]
        accumulated_response = stream_result["accumulated_response"]

        # 流结束后的处理
        debug_log(f"\n流结束统计:")
        debug_log(f"  总chunks: {chunk_count}")
        debug_log(f"  思考chunks: {reasoning_chunks}")
        debug_log(f"  内容chunks: {content_chunks}")
        debug_log(f"  工具chunks: {tool_chunks}")
        debug_log(f"  收集到的思考: {len(current_thinking)} 字符")
        debug_log(f"  收集到的正文: {len(full_response)} 字符")
        debug_log(f"  收集到的工具: {len(tool_calls)} 个")
        
        # 结束未完成的流
        if in_thinking and not thinking_ended:
            sender('thinking_end', {'full_content': current_thinking})
            await asyncio.sleep(0.1)
            
        
        # 确保text_end事件被发送
        if text_started and text_has_content:
            debug_log(f"发送text_end事件，完整内容长度: {len(full_response)}")
            sender('text_end', {'full_content': full_response})
            await asyncio.sleep(0.1)
            text_streaming = False
            
            if full_response.strip():
                debug_log(f"流式文本内容长度: {len(full_response)} 字符")
        
        # 检测是否有格式错误的工具调用
        if not tool_calls and full_response and AUTO_FIX_TOOL_CALL:
            if detect_malformed_tool_call(full_response):
                auto_fix_attempts += 1
                
                if auto_fix_attempts <= AUTO_FIX_MAX_ATTEMPTS:
                    debug_log(f"检测到格式错误的工具调用，尝试自动修复 (尝试 {auto_fix_attempts}/{AUTO_FIX_MAX_ATTEMPTS})")
                    
                    fix_message = tr("task_main.auto_fix_instruction")

                    sender('system_message', {
                        'content': tr("task_main.auto_fix_notice", message=fix_message)
                    })
                    
                    messages.append({
                        "role": "user",
                        "content": fix_message
                    })
                    
                    await asyncio.sleep(1)
                    continue
                else:
                    debug_log(f"自动修复尝试已达上限 ({AUTO_FIX_MAX_ATTEMPTS})")
                    sender('system_message', {
                        'content': tr("task_main.auto_fix_failed")
                    })
                    break
        
        # 构建助手消息（用于API继续对话）
        assistant_content_parts = []
        
        if full_response:
            assistant_content_parts.append(full_response)
        
        assistant_content = "\n".join(assistant_content_parts) if assistant_content_parts else ""
        
        # 行内引用：校验 marker、剥离无效引用，已用来源挂到消息 metadata 一并落库
        round_citations = []
        if assistant_content and ("【cite:" in assistant_content or "【file:" in assistant_content):
            try:
                from modules.citations import finalize_message_citations, get_registry
                assistant_content, round_citations = finalize_message_citations(
                    assistant_content,
                    get_registry(web_terminal),
                    str(getattr(web_terminal, "project_path", "") or ""),
                    allow_files=is_agent,
                )
                for ann in round_citations:
                    if ann.get("id"):
                        task_citations[ann["id"]] = ann
            except Exception as _cite_exc:
                debug_log(f"[Citations] 行内引用处理失败，保留原文: {_cite_exc}")
                round_citations = []
        
        # Responses 通道（通用）：取出本轮收集的加密 reasoning items（读后清空防残留），
        # 内存消息与落盘 metadata 各挂一份，供后续轮次回插上下文
        responses_reasoning_items = getattr(web_terminal.api_client, "last_responses_reasoning_items", None)
        if responses_reasoning_items:
            try:
                web_terminal.api_client.last_responses_reasoning_items = None
            except Exception:
                pass

        # 添加到消息历史（用于API继续对话，不保存到文件）
        assistant_message = {
            "role": "assistant",
            "content": assistant_content,
            # thinking 模式下，reasoning_content 需要原样回传；即使该轮为空字符串也保留字段
            "reasoning_content": current_thinking or "",
        }
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        if responses_reasoning_items:
            assistant_message["responses_reasoning_items"] = responses_reasoning_items

        messages.append(assistant_message)
        if assistant_content or current_thinking or tool_calls:
            assistant_metadata = {"citations": round_citations} if round_citations else {}
            if responses_reasoning_items:
                assistant_metadata["responses_reasoning_items"] = responses_reasoning_items
            web_terminal.context_manager.add_conversation(
                "assistant",
                assistant_content,
                tool_calls=tool_calls if tool_calls else None,
                reasoning_content=current_thinking or "",
                metadata=assistant_metadata or None,
                message_id=getattr(getattr(web_terminal, "_stream_partial", None), "message_id", None),
            )
        partial = getattr(web_terminal, "_stream_partial", None)
        if partial is not None:
            partial.committed = True
        
        # 为下一轮迭代重置流状态标志，但保留 full_response 供上面保存使用
        text_streaming = False
        text_started = False
        text_has_content = False
        full_response = ""

        if not tool_calls:
            # 没有工具时也兑现运行中收到的请求，不能等一个不会出现的工具。
            requested_result = await run_requested_compression(
                web_terminal=web_terminal, workspace=workspace, conversation_id=conversation_id,
                username=username, task_id=client_sid, sender=sender,
            )
            if requested_result is not None:
                await continue_after_compression(requested_result)
                return
            debug_log("没有工具调用，结束迭代")
            # === 目标模式：turn 结束拦截 ===
            try:
                if goal_is_active(workspace, conversation_id):
                    goal_result = await handle_goal_after_turn(
                        web_terminal=web_terminal,
                        workspace=workspace,
                        messages=messages,
                        sender=sender,
                        conversation_id=conversation_id,
                        assistant_content=assistant_content,
                        made_tool_call=goal_segment_made_tool_call,
                        total_tool_calls=total_tool_calls,
                    )
                    action = goal_result.get("action")
                    if action == "continue":
                        # 已注入续命 user 消息：上一段 assistant work 到此结束，
                        # 后续计时切换到刚注入的 goal_review 消息。
                        finalize_user_work_timer()
                        switch_current_work_timer_to_latest_user()
                        goal_segment_made_tool_call = False
                        is_first_iteration = False
                        debug_log(f"[Goal] 续命：{goal_result.get('message', '')[:80]}")
                        continue
                    if action == "done":
                        debug_log("[Goal] 目标已达成，结束任务")
                    elif action == "stop":
                        debug_log(f"[Goal] 目标停止：{goal_result.get('reason')}")
            except Exception as exc:
                debug_log(f"[Goal] turn 结束处理失败: {exc}")

            # 多智能体模式：没有工具调用时，先消费子智能体待转发到主对话的消息；
            # 如果有新消息注入，继续迭代让 Team Leader 响应，而不是直接结束任务。
            if getattr(web_terminal, "sub_agent_manager", None) is not None:
                injected_count = await process_multi_agent_master_messages(
                    messages=messages,
                    inline=False,
                    web_terminal=web_terminal,
                    sender=sender,
                    debug_log=debug_log,
                )
                ma_debug(
                    "no_tool_call_turn_process_ma_messages",
                    conversation_id=conversation_id,
                    injected_count=injected_count,
                )
                if injected_count:
                    debug_log(f"[MultiAgent] no-tool-call turn 注入 {injected_count} 条子智能体消息，继续迭代")
                    is_first_iteration = False
                    continue

            from server.runtime_guidance import inject_pending_guidance
            if inject_pending_guidance(
                web_terminal=web_terminal, messages=messages, username=username,
                task_id=client_sid, sender=sender, conversation_id=conversation_id,
                close_if_empty=True,
            ):
                is_first_iteration = False
                continue
            break

        # 目标模式：本轮段确实产生了工具调用
        goal_segment_made_tool_call = True

        # 检查连续相同工具调用
        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            
            if tool_name == last_tool_name:
                consecutive_same_tool[tool_name] += 1
                
                if (
                    MAX_CONSECUTIVE_SAME_TOOL is not None
                    and consecutive_same_tool[tool_name] >= MAX_CONSECUTIVE_SAME_TOOL
                ):
                    debug_log(f"警告: 连续调用相同工具 {tool_name} 已达 {MAX_CONSECUTIVE_SAME_TOOL} 次")
                    sender('system_message', {
                        'content': tr("task_main.repeat_tool_warning", tool=tool_name, limit=MAX_CONSECUTIVE_SAME_TOOL)
                    })
                    
                    if consecutive_same_tool[tool_name] >= MAX_CONSECUTIVE_SAME_TOOL + 2:
                        debug_log(f"终止: 工具 {tool_name} 调用次数过多")
                        sender('system_message', {
                            'content': tr("task_main.repeat_tool_terminated", tool=tool_name)
                        })
                        break
            else:
                consecutive_same_tool.clear()
                consecutive_same_tool[tool_name] = 1
            
            last_tool_name = tool_name

        # 更新统计
        total_tool_calls += len(tool_calls)
        try:
            if goal_is_active(workspace, conversation_id):
                emit_goal_progress(
                    web_terminal=web_terminal,
                    workspace=workspace,
                    sender=sender,
                    conversation_id=conversation_id,
                    total_tool_calls=total_tool_calls,
                )
        except Exception as exc:
            debug_log(f"[Goal] 进度广播失败: {exc}")

        # 执行每个工具
        tool_loop_result = await execute_tool_calls(
            web_terminal=web_terminal,
            tool_calls=tool_calls,
            sender=sender,
            messages=messages,
            client_sid=client_sid,
            username=username,
            iteration=iteration,
            conversation_id=conversation_id,
            last_tool_call_time=last_tool_call_time,
            process_background_command_updates=process_background_command_updates,
            process_multi_agent_master_messages=process_multi_agent_master_messages,
            get_stop_flag=get_stop_flag,
            clear_stop_flag=clear_stop_flag,
            workspace=workspace,
        )
        last_tool_call_time = tool_loop_result.get("last_tool_call_time", last_tool_call_time)
        if tool_loop_result.get("stopped"):
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("stopped")
            return
        if tool_loop_result.get("approval_rejected"):
            sender('task_stopped', {
                'message': tool_loop_result.get("approval_message") or tr("tool_loop.rejected_by_user"),
                'reason': 'approval_rejected',
                'conversation_id': conversation_id,
                'has_running_sub_agents': False,
                'has_running_background_commands': False,
                'has_running_multi_agent': False
            })
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("approval_rejected")
            return
        if tool_loop_result.get("deep_compressed"):
            await continue_after_compression(tool_loop_result.get("deep_result") or {})
            return
        
        # 标记不再是第一次迭代
        is_first_iteration = False

        # 多智能体模式：在进入下一轮模型调用前，消费子智能体最新输出到主对话
        if getattr(web_terminal, "sub_agent_manager", None) is not None:
            await process_multi_agent_master_messages(
                messages=messages,
                inline=False,
                web_terminal=web_terminal,
                sender=sender,
                debug_log=debug_log,
            )

    
    # 原子关闭请求入口前再取一次，覆盖最终回复/目标审核期间到达的点击。
    requested_result = await run_requested_compression(
        web_terminal=web_terminal, workspace=workspace, conversation_id=conversation_id,
        username=username, task_id=client_sid, sender=sender, close_if_empty=True,
    )
    if requested_result is not None:
        await continue_after_compression(requested_result)
        return

    from modules.thinking_summary import finish_work
    await finish_work(web_terminal)

    if not is_agent:
        finalize_user_work_timer()
        sender('task_complete', {
            'total_iterations': total_iterations, 'total_tool_calls': total_tool_calls,
            'auto_fix_attempts': auto_fix_attempts, 'citations': list(task_citations.values()),
            'has_running_sub_agents': False, 'has_running_background_commands': False,
            'has_running_multi_agent': False,
        })
        return

    # 最终统计
    # 多智能体模式：主对话任务结束，解除活跃标记
    web_terminal._multi_agent_main_task_active = False

    debug_log(f"\n{'='*40}")
    debug_log(f"任务完成统计:")
    debug_log(f"  总迭代次数: {total_iterations}")
    debug_log(f"  总工具调用: {total_tool_calls}")
    debug_log(f"  自动修复尝试: {auto_fix_attempts}")
    debug_log(f"  累积响应: {len(accumulated_response)} 字符")
    debug_log(f"{'='*40}\n")
    
    # 检查是否有后台运行的子智能体或待通知的完成任务
    manager = getattr(web_terminal, "sub_agent_manager", None)
    has_running_sub_agents = False
    has_running_multi_agent = False
    has_pending_ma_messages = False
    bg_manager = getattr(web_terminal, "background_command_manager", None)
    has_running_background_commands = False
    if manager:
        try:
            manager.reconcile_task_states(conversation_id=conversation_id)
        except Exception as exc:
            debug_log(f"[SubAgent] reconcile_task_states failed: {exc}")
        if not hasattr(web_terminal, "_announced_sub_agent_tasks"):
            web_terminal._announced_sub_agent_tasks = set()

        # 多智能体模式：检测是否还有真正运行中的实例，以及是否有尚未消费的 pending 消息。
        # idle 实例已完成当前输出、保留上下文等待后续指令；pending 消息会在主任务
        # 结束后由 poll_multi_agent_notifications 继续消费并触发新一轮工作。
        has_pending_ma_messages = False
        if manager:
            state = manager.get_multi_agent_state(conversation_id)
            if state:
                has_running_multi_agent = any(a.status == "running" for a in state.list_all())
                has_pending_ma_messages = state.has_pending_master_messages()

    # 检查是否有后台 run_command 或待通知任务
    if bg_manager and conversation_id:
        try:
            bg_manager.reconcile_stale_records(conversation_id=conversation_id)
        except Exception as exc:
            debug_log(f"[BgCmdDebug] reconcile_stale_records failed: {exc}")
        waiting_items = bg_manager.list_waiting_items(conversation_id)
        if waiting_items:
            has_running_background_commands = True
            # 与子智能体完全复用同一 waiting 事件（前端已有稳定处理链路）
            sender('sub_agent_waiting', _build_shared_waiting_payload(waiting_items))

    # 传统后台任务通知池：只处理 run_in_background=True 的子智能体 / 后台 run_command。
    needs_completion_poll = (
        has_running_sub_agents
        or has_running_background_commands
        or _has_pending_completion_work(
            web_terminal=web_terminal, conversation_id=conversation_id
        )
        or _has_pending_workflow_notices(
            web_terminal=web_terminal, conversation_id=conversation_id
        )
    )
    if needs_completion_poll:
        ma_debug(
            "completion_poll_start",
            conversation_id=conversation_id,
            has_running_sub_agents=has_running_sub_agents,
            has_running_background_commands=has_running_background_commands,
        )
        def run_completion_poll():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(poll_completion_notifications(
                    web_terminal=web_terminal,
                    workspace=workspace,
                    conversation_id=conversation_id,
                    client_sid=client_sid,
                    username=username
                ))
            finally:
                loop.close()

        run_background(run_completion_poll)

    # 多智能体模式独立通知池：处理 running 实例 / idle 实例待消费的 pending 消息。
    # 与传统后台任务完全分离，避免两者竞争 create_chat_task 的单工作区互斥。
    needs_ma_poll = has_running_multi_agent or has_pending_ma_messages
    ma_debug(
        "ma_poll_decision",
        conversation_id=conversation_id,
        has_running_multi_agent=has_running_multi_agent,
        has_pending_ma_messages=has_pending_ma_messages,
        needs_ma_poll=needs_ma_poll,
    )
    if needs_ma_poll:
        ma_debug(
            "ma_poll_start",
            conversation_id=conversation_id,
            has_running_multi_agent=has_running_multi_agent,
            has_pending_ma_messages=has_pending_ma_messages,
        )
        def run_ma_poll():
            import threading as _t
            ma_debug(
                "ma_poll_thread_enter",
                conversation_id=conversation_id,
                thread_id=_t.get_ident(),
            )
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(poll_multi_agent_notifications(
                    web_terminal=web_terminal,
                    workspace=workspace,
                    conversation_id=conversation_id,
                    client_sid=client_sid,
                    username=username
                ))
            except Exception as exc:
                ma_debug(
                    "ma_poll_thread_exception",
                    conversation_id=conversation_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            else:
                ma_debug("ma_poll_thread_exit_ok", conversation_id=conversation_id)
            finally:
                loop.close()
                ma_debug("ma_poll_thread_finally", conversation_id=conversation_id)

        run_background(run_ma_poll)

    if not needs_completion_poll and not needs_ma_poll:
        ma_debug(
            "completion_poll_skip",
            conversation_id=conversation_id,
            reason="no_running_background_or_multi_agent",
        )

    has_running_completion_jobs = has_running_sub_agents or has_running_background_commands

    # 任务结束时清理残留的 pending runtime mode（权限/执行环境/网络权限）。
    # 场景：用户在模型最后一次输出（无工具调用）后、task 仍为 running 状态时切换了
    # 权限/执行环境，此时会进入 queue_*_change 路径只设置 pending，但 task 随即自然
    # 结束，不会进入 execute_tool_calls → apply_pending_runtime_mode_changes。
    # 若不在此清理，pending 会残留到下一次工作首次工具调用时才被应用，
    # 并把"变更通知"作为 user 消息注入到新一轮对话中（bug）。
    # 正确行为：task 结束时立即应用 pending（等同空闲期切换），此处不生成通知；
    # 由于通知基线（runtime_mode_baseline）未更新，下一条真实 user 消息时会通过
    # drift 检测一次性补发切换通知（见 user_message 回显后的注入点）。
    try:
        if hasattr(web_terminal, "apply_pending_runtime_mode_changes"):
            residual_notices = web_terminal.apply_pending_runtime_mode_changes() or []
            if residual_notices:
                debug_log(
                    "[RuntimeMode] task 结束时应用残留 pending runtime mode，丢弃通知: "
                    f"{[n.get('source') if isinstance(n, dict) else n for n in residual_notices]}"
                )
    except Exception as exc:
        debug_log(f"[RuntimeMode] task 结束时清理 pending runtime mode 失败: {exc}")

    # 未消费引导由 TaskManager 收尾时连同附件返回持久队列，不能取出后丢弃。
    pending_runtime_guidance_messages: List[str] = []

    # 发送完成事件（如果有后台完成任务在运行，前端会保持等待状态）
    if not has_running_completion_jobs:
        # 对话真正空闲：把 work_timer 持久化到后端，避免刷新后恢复成「工作中」
        try:
            if web_terminal and conversation_id:
                finalize_conversation_work_timer(
                    web_terminal,
                    conversation_id,
                    finished_at=datetime.now().isoformat(),
                )
        except Exception:
            pass
        finalize_user_work_timer()
        finalize_run_versioning_checkpoint("completed")
    else:
        finalize_run_versioning_checkpoint("waiting_background")
    sender('task_complete', {
        'total_iterations': total_iterations,
        'total_tool_calls': total_tool_calls,
        'auto_fix_attempts': auto_fix_attempts,
        # 沿用子智能体字段，确保前端直接走已验证通路
        'has_running_sub_agents': has_running_completion_jobs,
        'has_running_background_commands': has_running_background_commands,
        # 多智能体模式：只要还有 running 实例或尚未消费的 pending 消息，
        # 前端就应保持运行态并继续轮询。
        'has_running_multi_agent': has_running_multi_agent or has_pending_ma_messages,
        'pending_runtime_guidance_messages': pending_runtime_guidance_messages,
        # 行内引用：本任务全部已用来源，前端挂到当前 assistant 消息渲染 chip
        'citations': list(task_citations.values()),
    })
