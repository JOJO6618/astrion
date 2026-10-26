from __future__ import annotations
import time
from typing import Any, Dict, List, Optional
from modules.i18n import tr


def _build_shared_waiting_payload(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """构建与子智能体一致的 waiting 事件载荷结构。"""
    normalized = []
    for item in items or []:
        summary = item.get('summary') or item.get('command') or '后台任务'
        normalized.append({
            'agent_id': item.get('agent_id') or item.get('command_id') or item.get('task_id'),
            'summary': summary,
        })
    return {
        'count': len(normalized),
        'tasks': normalized,
    }

def _build_sub_agent_notice_text(update: Dict[str, Any], *, summary_override: Optional[str] = None) -> str:
    """根据子智能体完成结果构建一条 user 通知文本（统一 [系统通知|sub_agent] 前缀）。

    update 通常是 task 的 final_result，其中 system_message 已经是一条完整的格式化通知
    （含 ✅/❌ 前缀、统计、运行秒数、summary、交付目录）。这里直接复用它，只补统一前缀，
    不再在外层重复拼 agent/运行秒数/交付目录，避免内容出现两遍。
    """
    system_message = (update.get("system_message") or "").strip()
    if not system_message:
        # 兜底：极少数情况下 final_result 没有 system_message，用最小信息拼一条
        agent_id = update.get("agent_id")
        summary = summary_override or update.get("summary") or update.get("message") or ""
        deliverables_dir = update.get("deliverables_dir", "") or ""
        lines = [tr("task_main.sub_agent_done_line", agent_id=agent_id, summary=summary)]
        msg = update.get("message")
        if msg:
            lines.append(str(msg))
        if deliverables_dir:
            lines.append(tr("task_main.deliverables_line", dir=deliverables_dir))
        system_message = "\n\n".join(lines)
    return f"[系统通知|sub_agent]\n{system_message}"

def _collect_pending_completion_notices(*, web_terminal, conversation_id: str) -> List[Dict[str, Any]]:
    """从子智能体 + 后台 run_command 两路统一取出所有待通知项（已按时间排序）。

    返回的每一项形如：
        {
            "kind": "sub_agent" | "background_command",
            "message": <user 通知文本>,
            "payload": <前端事件 extra payload>,
            "sort_key": <用于跨两路排序的时间戳>,
        }
    取出时即就地标记 notified，确保不会被下一轮重复消费。
    """
    notices: List[Dict[str, Any]] = []

    # 2) 后台 run_command
    bg_manager = getattr(web_terminal, "background_command_manager", None)
    if bg_manager:
        try:
            bg_updates = bg_manager.poll_updates(conversation_id=conversation_id)
        except Exception:
            bg_updates = []
        for update in bg_updates or []:
            command_id = update.get("command_id")
            command = update.get("command") or ""
            output = update.get("output") or ""
            return_code = update.get("return_code")
            try:
                bg_manager.mark_notified(str(command_id))
            except Exception:
                pass
            # 统一 [系统通知|background_command] 前缀；正文给出命令、退出码、输出
            header = tr("background_tasks.done_header")
            if command:
                header += "\n\n" + tr("background_tasks.command_line", command=command)
            if return_code is not None:
                header += "\n" + tr("background_tasks.return_code_line", code=return_code)
            body = output if output else "[no_output]"
            message_text = f"[系统通知|background_command]\n{header}\n\n{tr('background_tasks.output_section')}\n{body}"
            notices.append({
                "kind": "background_command",
                "message": message_text,
                "payload": {
                    # 与子智能体完成通知完全复用同一前端通道/处理逻辑
                    "sub_agent_notice": True,
                    "message_source": "background_command",
                    "background_command_notice": True,
                    "command_id": command_id,
                },
                "sort_key": update.get("updated_at") or time.time(),
            })

    # 3) 工作流柔性通知（撞限询问 / 用户 slash 退出等）
    try:
        from modules.workflow_state_manager import WorkflowStateManager

        _wsm = WorkflowStateManager(web_terminal.data_dir, conversation_id)
        wf_updates = _wsm.poll_notices()
    except Exception:
        wf_updates = []
        _wsm = None
    for notice in wf_updates or []:
        notice_text = str((notice or {}).get("message") or "").strip()
        if not notice_text:
            continue
        notices.append({
            "kind": "workflow",
            "message": notice_text,
            "payload": {
                "sub_agent_notice": True,
                "message_source": "workflow",
                "workflow_notice": True,
                "conversation_id": conversation_id,
            },
            "sort_key": notice.get("created_at") or time.time(),
        })

    notices.sort(key=lambda item: item.get("sort_key") or 0)
    return notices

def _rollback_completion_notice_marks(*, web_terminal, notices: List[Dict[str, Any]]) -> None:
    """派发失败时回滚 _collect_pending_completion_notices 的就地标记，
    避免通知被标记为已消费却从未派发（静默丢失）。
    """
    bg_manager = getattr(web_terminal, "background_command_manager", None)
    wf_restore: List[Dict[str, Any]] = []
    for item in notices or []:
        payload = item.get("payload") or {}
        if item.get("kind") == "background_command":
            command_id = payload.get("command_id")
            if not command_id or not bg_manager:
                continue
            try:
                with bg_manager._lock:
                    rec = bg_manager._records.get(str(command_id))
                    if rec:
                        rec["notified"] = False
            except Exception:
                pass
        elif item.get("kind") == "workflow":
            wf_restore.append(item)
    # 工作流通知：按 conversation 分组统一放回池（restore_notices 会保留现有未消费项）
    if wf_restore:
        try:
            from modules.workflow_state_manager import WorkflowStateManager

            _groups: Dict[str, List[Dict[str, Any]]] = {}
            for item in wf_restore:
                cid = str((item.get("payload") or {}).get("conversation_id") or "")
                if not cid:
                    continue
                _groups.setdefault(cid, []).append({
                    "type": "workflow",
                    "message": item.get("message") or "",
                    "created_at": item.get("sort_key") or time.time(),
                })
            for cid, items in _groups.items():
                WorkflowStateManager(web_terminal.data_dir, cid).restore_notices(items)
        except Exception:
            pass

def _has_pending_completion_work(*, web_terminal, conversation_id: str) -> bool:
    """是否还有运行中或待通知的传统后台任务（子智能体/后台 run_command）。

    多智能体模式下的 pending 消息由独立的 poll_multi_agent_notifications 处理，
    不再混入本函数。
    """
    bg_manager = getattr(web_terminal, "background_command_manager", None)
    if bg_manager:
        try:
            if bg_manager.has_pending_for_conversation(conversation_id):
                return True
        except Exception:
            pass
    return False

def _has_pending_workflow_notices(*, web_terminal, conversation_id: str) -> bool:
    """是否还有未消费的工作流柔性通知（撞限询问 / 用户退出等）。"""
    try:
        from modules.workflow_state_manager import WorkflowStateManager

        return WorkflowStateManager(web_terminal.data_dir, conversation_id).has_pending_notices()
    except Exception:
        return False
