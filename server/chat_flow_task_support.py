from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from modules.sub_agent import TERMINAL_STATUSES
from modules.multi_agent.debug_logger import ma_debug


_VALID_SOURCES = {
    "guidance",
    "notify",
    "user",
    "presend",
    "sub_agent",
    "background_command",
    "goal",
    "goal_prompt",
    "goal_review",
    "compression",
    "compression_handoff",
    "permission",
    "sandbox",
    "skill",
    "permission_change",
    "execution_change",
    "network_change",
    "work_mode_change",
    "file_paths",
    "workflow",
}


def _runtime_message_ui_defaults(src: str, *, inline: bool = False) -> Dict[str, Any]:
    """Return UI metadata for model-facing user messages injected by the runtime."""
    normalized = str(src or "").strip().lower()
    if normalized in {"skill", "goal_prompt", "permission", "sandbox", "file_paths"}:
        return {"visibility": "hidden", "starts_work": False}
    if normalized in {"guidance", "notify"}:
        return {"visibility": "compact", "starts_work": False}
    # 运行期模式（权限/执行环境/网络权限/运行模式）切换通知：仅通知，不开启新一轮工作。
    if normalized in {"permission_change", "execution_change", "network_change", "work_mode_change"}:
        return {"visibility": "compact", "starts_work": False}
    # 后台完成通知 / 运行期压缩续接：属于当前这轮工作的延续，不开启新的工作头与计时器，
    # 也不暂停上一轮计时器。与 task_main 的 _user_message_ui_defaults 保持一致，
    # 确保运行期直接渲染与刷新后从历史加载行为相同。
    if normalized in {"sub_agent", "background_command", "compression", "compression_handoff"}:
        return {"visibility": "compact", "starts_work": False}
    # 工作流运行期通知（退出/柔性通知 inline 注入）：延续当前工作段的紧凑通知，
    # 与其他系统通知一致走 compact 渲染；闲时任务入口派发走 task_main 的
    # _user_message_ui_defaults（chat + starts_work=True，开启新一轮工作段）。
    if normalized == "workflow":
        return {"visibility": "compact", "starts_work": False}
    # 目标审核续命：开启新一轮工作。
    if normalized == "goal_review":
        return {"visibility": "compact", "starts_work": True}
    if normalized == "presend":
        return {"visibility": "compact", "starts_work": True}
    if normalized == "goal":
        # Backward-compatible fallback for old callers.  Inline goal injections are
        # current-segment hints; non-inline goal injections start the next segment.
        return {"visibility": "compact", "starts_work": not bool(inline)}
    return {"visibility": "chat", "starts_work": normalized == "user"}


def inject_runtime_user_message(
    *,
    web_terminal,
    messages: Optional[List[Dict]],
    text: str,
    source: str,
    sender: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    conversation_id: Optional[str] = None,
    after_tool_call_id: Optional[str] = None,
    inline: bool,
    extra_metadata: Optional[Dict[str, Any]] = None,
    persist: bool = True,
) -> Optional[str]:
    """运行期/空闲期统一向对话插入一条 user 消息（带 [系统通知|{source}] 前缀）。

    - inline=True 表示运行期插入（仅作为通知，不主动触发新一轮回复）。
    - inline=False 表示空闲期插入（等价于一条用户消息，由调用方决定是否启动新一轮回复）。
    - messages=None 时仅持久化到对话历史并广播前端（无运行中的 messages 列表可写）。
    """
    raw = "" if text is None else str(text).strip()
    if not raw:
        return None

    src = str(source or "").strip().lower()
    if src not in _VALID_SOURCES:
        src = "guidance"

    formatted = f"[系统通知|{src}]\n{raw}"

    metadata: Dict[str, Any] = {
        "runtime_injected": True,
        "source": src,
        "inline": inline,
        "is_auto_generated": True,
        "auto_message_type": f"runtime_{src}",
        "message_source": src,
        "runtime_guidance": True,
        "runtime_guidance_original": raw,
    }
    metadata.update(_runtime_message_ui_defaults(src, inline=inline))
    if extra_metadata:
        metadata.update({k: v for k, v in extra_metadata.items() if v is not None})
    if metadata.get("starts_work") is True and not isinstance(metadata.get("work_timer"), dict):
        metadata["work_timer"] = {
            "status": "working",
            "started_at": datetime.now().isoformat(),
        }

    if persist:
        try:
            ctx_manager = getattr(web_terminal, "context_manager", None)
            if ctx_manager is not None:
                ctx_manager.add_conversation("user", formatted, metadata=metadata)
        except Exception:
            pass

    if messages is not None:
        insert_index = len(messages)
        if after_tool_call_id:
            for idx, msg in enumerate(messages):
                if msg.get("role") == "tool" and msg.get("tool_call_id") == after_tool_call_id:
                    # 跳过紧随其后的所有 tool 消息，避免把 user 插进同一 assistant.tool_calls 的 tool 序列中间，
                    # 否则会触发 "insufficient tool messages following tool_calls" 错误。
                    end = idx + 1
                    while end < len(messages) and messages[end].get("role") == "tool":
                        end += 1
                    insert_index = end
                    break
        messages.insert(insert_index, {
            "role": "user",
            "content": formatted,
        })

    if callable(sender):
        payload: Dict[str, Any] = {
            "message": formatted,
            "content": formatted,
            "conversation_id": conversation_id,
            "inline": inline,
            "source": src,
            "message_source": src,
            "visibility": metadata.get("visibility"),
            "starts_work": metadata.get("starts_work"),
            "metadata": metadata,
            "runtime_guidance": True,
            "runtime_guidance_original": raw,
        }
        # 关键：不能把子智能体任务 id 透传为顶层 task_id——本事件由主任务 sender
        # 发出、走主任务事件流，若顶层 task_id 是子任务 id，sender 的 setdefault
        # 不会覆盖，前端轮询的「任务归属守卫」（task_id !== currentTaskId 即丢弃）
        # 会把这条通知扔掉；叠加 socket 通道在轮询模式下不渲染，导致运行期 inline
        # 完成通知只能刷新后从历史看到（2026-08-12 定位，后台指令不受影响是因为
        # 它透传的是 command_id 而非 task_id）。子任务 id 仍保留在 metadata 中。
        for key in ("command_id",):
            value = (extra_metadata or {}).get(key)
            if value is not None:
                payload[key] = value
        try:
            sender("user_message", payload)
        except Exception:
            pass

    return formatted


async def process_sub_agent_updates(*, messages: List[Dict], inline: bool = False, after_tool_call_id: Optional[str] = None, web_terminal, sender, debug_log):
    """轮询子智能体任务并通知前端，并把结果插入当前对话上下文。"""
    manager = getattr(web_terminal, "sub_agent_manager", None)
    if not manager:
        return

    # 获取已通知的任务集合
    if not hasattr(web_terminal, '_announced_sub_agent_tasks'):
        web_terminal._announced_sub_agent_tasks = set()

    try:
        updates = manager.poll_updates()
        debug_log(f"[SubAgent] poll inline={inline} after_tool_call_id={after_tool_call_id} updates={len(updates)}")
    except Exception as exc:
        debug_log(f"子智能体状态检查失败: {exc}")
        return

    # 兜底：如果 poll_updates 没命中，但任务已被别处更新为终态且未通知，补发一次
    if not updates:
        synthesized = []
        try:
            for task_id, task in getattr(manager, "tasks", {}).items():
                if not isinstance(task, dict):
                    continue
                status = task.get("status")
                if status not in TERMINAL_STATUSES.union({"terminated"}):
                    continue
                if task.get("notified"):
                    continue
                if task.get("multi_agent_mode"):
                    continue
                task_conv_id = task.get("conversation_id")
                current_conv_id = getattr(getattr(web_terminal, "context_manager", None), "current_conversation_id", None)
                if task_conv_id and current_conv_id and task_conv_id != current_conv_id:
                    continue
                final_result = task.get("final_result")
                if not final_result:
                    try:
                        final_result = manager._check_task_status(task)
                    except Exception:
                        final_result = None
                if isinstance(final_result, dict):
                    synthesized.append(final_result)
        except Exception as exc:
            debug_log(f"[SubAgent] synthesized updates failed: {exc}")
            synthesized = []

        if synthesized:
            updates = synthesized
            debug_log(f"[SubAgent] synthesized updates count={len(updates)}")

    if inline and not hasattr(web_terminal, "_inline_sub_agent_notified"):
        web_terminal._inline_sub_agent_notified = set()

    for update in updates:
        task_id = update.get("task_id")
        task_info = manager.tasks.get(task_id) if task_id else None
        current_conv_id = getattr(getattr(web_terminal, "context_manager", None), "current_conversation_id", None)
        task_conv_id = task_info.get("conversation_id") if isinstance(task_info, dict) else None
        if task_conv_id and current_conv_id and task_conv_id != current_conv_id:
            debug_log(f"[SubAgent] 跳过非当前对话任务: task={task_id} conv={task_conv_id} current={current_conv_id}")
            continue
        if task_id and task_info is None:
            debug_log(f"[SubAgent] 找不到任务详情，跳过: task={task_id}")
            continue

        # 检查是否已经通知过这个任务
        if task_id and task_id in web_terminal._announced_sub_agent_tasks:
            debug_log(f"[SubAgent] 任务 {task_id} 已通知过，跳过")
            continue
        if isinstance(task_info, dict) and task_info.get("notified"):
            debug_log(f"[SubAgent] 任务 {task_id} notified=true，跳过")
            continue

        message = update.get("system_message")
        if not message:
            debug_log(f"[SubAgent] update missing system_message: task={task_id} keys={list(update.keys())}")
            continue

        if inline:
            inline_key = ("task", task_id) if task_id else ("msg", message)
            if inline_key in web_terminal._inline_sub_agent_notified:
                debug_log(f"[SubAgent] inline 通知已发送，跳过: key={inline_key}")
                continue

        debug_log(f"[SubAgent] update task={task_id} inline={inline} msg={message}")

        # 标记任务已通知
        if task_id:
            web_terminal._announced_sub_agent_tasks.add(task_id)
            if isinstance(task_info, dict):
                task_info["notified"] = True
                task_info["updated_at"] = time.time()
                try:
                    manager._save_state()
                except Exception as exc:
                    debug_log(f"[SubAgent] 保存通知状态失败: {exc}")

        conversation_id = getattr(getattr(web_terminal, "context_manager", None), "current_conversation_id", None)
        inject_runtime_user_message(
            web_terminal=web_terminal,
            messages=messages,
            text=message,
            source="sub_agent",
            sender=sender,
            conversation_id=conversation_id,
            after_tool_call_id=after_tool_call_id,
            inline=inline,
            extra_metadata={"task_id": task_id},
        )
        if inline:
            web_terminal._inline_sub_agent_notified.add(inline_key)
        debug_log(f"[SubAgent] 插入子智能体通知 after_tool_call_id={after_tool_call_id} inline={inline}")


async def process_workflow_updates(
    *,
    web_terminal,
    messages=None,
    sender=None,
    inline: bool,
    after_tool_call_id: Optional[str] = None,
    debug_log=None,
) -> bool:
    """Drain workflow pending notices and inject them as runtime user messages.

    Mirrors process_sub_agent_updates for the workflow lane. ``poll_notices()``
    returns the queued notices and clears the pool; injection follows the same
    fire-and-forget convention as the sub-agent lane (no restore on failure).
    """
    _log = debug_log if callable(debug_log) else (lambda msg: None)
    try:
        from modules.workflow_state_manager import WorkflowStateManager

        conversation_id = getattr(
            getattr(web_terminal, "context_manager", None),
            "current_conversation_id",
            None,
        )
        if not conversation_id:
            return False
        manager = WorkflowStateManager(web_terminal.data_dir, conversation_id)
        updates = manager.poll_notices()
    except Exception as exc:  # noqa: BLE001
        _log(f"[Workflow] Failed to read workflow updates: {exc}")
        return False
    if not updates:
        return False

    inserted = 0
    for notice in updates:
        text = str((notice or {}).get("message") or "").strip()
        if not text:
            continue
        try:
            inject_runtime_user_message(
                web_terminal=web_terminal,
                messages=messages,
                text=text,
                source="workflow",
                sender=sender,
                conversation_id=conversation_id,
                after_tool_call_id=after_tool_call_id,
                inline=inline,
            )
            inserted += 1
        except Exception as exc:  # noqa: BLE001
            _log(f"[Workflow] Failed to inject workflow update: {exc}")
    return inserted > 0


async def process_background_command_updates(*, messages: List[Dict], inline: bool = False, after_tool_call_id: Optional[str] = None, web_terminal, sender, debug_log):
    """轮询后台 run_command 完成状态并发送 system 消息通知。"""
    manager = getattr(web_terminal, "background_command_manager", None)
    if not manager:
        debug_log("[BgCmdDebug] process_background_command_updates skipped: manager 不存在")
        return

    conversation_id = getattr(getattr(web_terminal, "context_manager", None), "current_conversation_id", None)
    debug_log(f"[BgCmdDebug] process_background_command_updates start inline={inline} after_tool_call_id={after_tool_call_id} conv={conversation_id}")
    try:
        updates = manager.poll_updates(conversation_id=conversation_id)
        debug_log(f"[BgCmdDebug] poll_updates returned {len(updates)} updates")
    except Exception as exc:
        debug_log(f"[BgCommand] poll 更新失败: {exc}")
        return

    if not updates:
        debug_log("[BgCmdDebug] no background command updates")
        return

    for update in updates:
        command_id = update.get("command_id")
        output = update.get("output") or ""
        message = "[后台 run_command 完成]\n" + (output if output else "[no_output]")
        status = update.get("status")
        debug_log(
            f"[BgCmdDebug] emit inline notice command_id={command_id} status={status} "
            f"output_len={len(output)} inline={inline}"
        )

        inject_runtime_user_message(
            web_terminal=web_terminal,
            messages=messages,
            text=message,
            source="background_command",
            sender=sender,
            conversation_id=conversation_id,
            after_tool_call_id=after_tool_call_id,
            inline=inline,
            extra_metadata={"command_id": command_id},
        )
        try:
            manager.mark_notified(str(command_id))
            debug_log(f"[BgCmdDebug] mark_notified success command_id={command_id}")
        except Exception:
            debug_log(f"[BgCmdDebug] mark_notified failed command_id={command_id}")
            pass



def _auto_message_type_for_multi_agent_subtype(subtype: Optional[str]) -> str:
    """把多智能体消息 subtype 映射为统一的 auto_message_type。"""
    if subtype == "progress_output":
        return "multi_agent_progress_output"
    if subtype == "completion_report":
        return "multi_agent_completion_report"
    if subtype == "ask_master":
        return "multi_agent_ask_master"
    return "multi_agent_output"


def inject_multi_agent_master_message(
    *,
    web_terminal,
    messages,
    text: str,
    sender,
    conversation_id: Optional[str] = None,
    inline: bool = True,
    after_tool_call_id: Optional[str] = None,
) -> Optional[str]:
    """把多智能体子智能体输出/消息以原生格式注入主对话，不添加 [系统通知|xxx] 前缀。"""
    raw = "" if text is None else str(text).strip()
    if not raw:
        return None

    ma_debug(
        "inject_multi_agent_master_message",
        conversation_id=conversation_id,
        text_preview=raw[:300],
        inline=inline,
        after_tool_call_id=after_tool_call_id,
    )
    # 解析标准格式以获取 subtype；解析失败时回退到通用类型
    try:
        from modules.multi_agent.state import parse_multi_agent_message
        parsed = parse_multi_agent_message(raw)
    except Exception:
        parsed = None
    subtype = parsed.get("subtype") if parsed else None
    auto_message_type = _auto_message_type_for_multi_agent_subtype(subtype)

    metadata = {
        "runtime_injected": True,
        "source": "sub_agent",
        "message_source": "sub_agent",
        "inline": inline,
        "is_auto_generated": True,
        "auto_message_type": auto_message_type,
        "multi_agent_message": True,
        "multi_agent_display_name": parsed.get("display_name") if parsed else None,
        "multi_agent_subtype": subtype,
        "visibility": "chat",
        "starts_work": False,
    }

    try:
        ctx_manager = getattr(web_terminal, "context_manager", None)
        if ctx_manager is not None:
            ctx_manager.add_conversation("user", raw, metadata=metadata)
            ma_debug(
                "inject_ma_message_saved",
                conversation_id=conversation_id,
                text_preview=raw[:200],
            )
    except Exception as exc:
        ma_debug("inject_ma_message_save_error", error=str(exc))

    if messages is not None:
        insert_index = len(messages)
        if after_tool_call_id:
            for idx, msg in enumerate(messages):
                if msg.get("role") == "tool" and msg.get("tool_call_id") == after_tool_call_id:
                    end = idx + 1
                    while end < len(messages) and messages[end].get("role") == "tool":
                        end += 1
                    insert_index = end
                    break
        messages.insert(insert_index, {"role": "user", "content": raw})
        ma_debug(
            "inject_ma_message_inserted",
            conversation_id=conversation_id,
            insert_index=insert_index,
            messages_len_after=len(messages),
            text_preview=raw[:200],
        )

    if callable(sender):
        payload = {
            "message": raw,
            "content": raw,
            "conversation_id": conversation_id,
            "inline": inline,
            "source": "sub_agent",
            "message_source": "sub_agent",
            "visibility": "chat",
            "starts_work": False,
            "auto_message_type": auto_message_type,
            "multi_agent_message": True,
            "multi_agent_display_name": parsed.get("display_name") if parsed else None,
            "multi_agent_subtype": subtype,
            "metadata": metadata,
            "runtime_injected": True,
        }
        try:
            sender("user_message", payload)
        except Exception:
            pass

    return raw


async def process_multi_agent_master_messages(
    *,
    web_terminal,
    messages,
    sender,
    debug_log,
    inline: bool = False,
    after_tool_call_id: Optional[str] = None,
) -> int:
    """从 MultiAgentState 取出待插入主对话的消息并注入。返回注入条数。"""
    manager = getattr(web_terminal, "sub_agent_manager", None)
    ma_debug(
        "process_ma_messages_enter",
        multi_agent_mode=getattr(web_terminal, "multi_agent_mode", False),
        has_manager=bool(manager),
        manager_id=id(manager) if manager else None,
        conversation_id=getattr(getattr(web_terminal, "context_manager", None), "current_conversation_id", None),
        inline=inline,
        after_tool_call_id=after_tool_call_id,
        messages_len_before=len(messages) if messages else 0,
    )
    if not getattr(web_terminal, "multi_agent_mode", False):
        return 0
    manager = getattr(web_terminal, "sub_agent_manager", None)
    if not manager:
        ma_debug("process_ma_messages_no_manager")
        return 0
    conversation_id = getattr(getattr(web_terminal, "context_manager", None), "current_conversation_id", None)
    if not conversation_id:
        ma_debug("process_ma_messages_no_conversation_id")
        return 0
    state = manager.get_multi_agent_state(conversation_id)
    if not state:
        ma_debug("process_ma_messages_no_state", conversation_id=conversation_id)
        return 0
    ma_debug("process_ma_messages_got_state", conversation_id=conversation_id, state_id=id(state))
    pending = state.drain_master_messages()
    if not pending:
        ma_debug("process_ma_messages_empty", conversation_id=conversation_id, state_id=id(state))
        return 0
    debug_log(f"[MultiAgent] draining {len(pending)} pending master messages")
    ma_debug(
        "process_multi_agent_master_messages",
        conversation_id=conversation_id,
        count=len(pending),
        previews=[str(m)[:200] for m in pending],
    )
    for msg in pending:
        inject_multi_agent_master_message(
            web_terminal=web_terminal,
            messages=messages,
            text=msg,
            sender=sender,
            conversation_id=conversation_id,
            inline=inline,
            after_tool_call_id=after_tool_call_id,
        )
    return len(pending)


async def wait_retry_delay(*, delay_seconds: int, client_sid: str, username: str, sender, get_stop_flag, clear_stop_flag) -> bool:
    """等待重试间隔，同时检查是否收到停止请求。"""
    if delay_seconds <= 0:
        return False
    deadline = time.time() + delay_seconds
    while time.time() < deadline:
        client_stop_info = get_stop_flag(client_sid, username, include_user=False)
        if client_stop_info:
            stop_requested = client_stop_info.get('stop', False) if isinstance(client_stop_info, dict) else client_stop_info
            if stop_requested:
                sender('task_stopped', {
                    'message': '命令执行被用户取消',
                    'reason': 'user_stop'
                })
                clear_stop_flag(client_sid, username)
                return True
        await asyncio.sleep(0.2)
    return False



def cancel_pending_tools(*, tool_calls_list, sender, messages):
    """为尚未返回结果的工具生成取消结果，防止缺失 tool_call_id 造成后续 400。"""
    if not tool_calls_list:
        return
    for tc in tool_calls_list:
        tc_id = tc.get("id")
        func_name = tc.get("function", {}).get("name")
        sender('update_action', {
            'preparing_id': tc_id,
            'status': 'cancelled',
            'result': {
                "success": False,
                "status": "cancelled",
                "message": "命令执行被用户取消",
                "tool": func_name
            }
        })
        if tc_id:
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "name": func_name,
                "content": "命令执行被用户取消",
            })
