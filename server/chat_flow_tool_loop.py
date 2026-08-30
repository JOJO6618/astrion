from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List

from .utils_common import debug_log
from .state import MONITOR_FILE_TOOLS, MONITOR_MEMORY_TOOLS, MONITOR_SNAPSHOT_CHAR_LIMIT, MONITOR_MEMORY_ENTRY_LIMIT
from .state import tool_approval_manager, user_question_manager, plan_approval_manager
from .monitor import cache_monitor_snapshot
from .security import compact_web_search_result
from .chat_flow_helpers import detect_tool_failure
from .chat_flow_runner_helpers import resolve_monitor_path, resolve_monitor_memory, capture_monitor_snapshot
from modules.i18n import tr
from utils.tool_result_formatter import (
    extract_mcp_content_for_context,
    format_tool_result_for_context,
)
from utils.context_manager import AUTO_SHALLOW_PLACEHOLDER
from config import TOOL_CALL_COOLDOWN
from modules.personalization_manager import load_personalization_config, resolve_context_compression_settings
from modules.auto_approval_service import run_auto_approval
from modules.user_question_manager import format_user_question_answer
from .deep_compression import run_deep_compression
from .chat_flow_task_support import inject_runtime_user_message, process_multi_agent_master_messages, process_workflow_updates


def _format_numbered_lines(lines: List[str], start_line_no: int) -> List[Dict[str, Any]]:
    return [
        {
            "line_no": start_line_no + idx,
            "content": line.rstrip("\n"),
        }
        for idx, line in enumerate(lines)
    ]


def _build_tool_approval_preview(web_terminal, function_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    args = arguments or {}
    preview: Dict[str, Any] = {
        "type": function_name,
        "tool_name": function_name,
        "arguments": args,
    }

    if function_name == "edit_file":
        file_path = args.get("file_path")
        replacements = args.get("replacements")
        preview["file_path"] = file_path
        if not file_path:
            preview["summary"] = tr("tool_loop.preview_missing_file_path")
            return preview
        if not isinstance(replacements, list) or not replacements:
            preview["summary"] = tr("tool_loop.preview_missing_replacements")
            return preview
        preview["replacement_groups"] = len(replacements)
        try:
            valid, err, full_path = web_terminal.file_manager._validate_path(str(file_path))
            if not valid or full_path is None:
                preview["summary"] = err or tr("tool_loop.preview_path_invalid")
                return preview
            resolved_path = str(Path(full_path))
            preview["resolved_path"] = resolved_path
            if not full_path.exists() or not full_path.is_file():
                preview["summary"] = tr("tool_loop.preview_file_not_found")
                return preview
            content = full_path.read_text(encoding="utf-8", errors="ignore")
            first = replacements[0] if isinstance(replacements[0], dict) else {}
            old_text = str(first.get("old_string") or "")
            new_text = str(first.get("new_string") or "")
            replace_all = first.get("replace_all", False)
            if not isinstance(replace_all, bool):
                preview["summary"] = tr("tool_loop.preview_replace_all_type")
                return preview
            short_old_text_notice = bool(old_text and len(old_text.splitlines()) < 3)
            if short_old_text_notice:
                preview["notice"] = tr("tool_loop.preview_short_old_notice")
            old_lines = old_text.splitlines()
            new_lines = new_text.splitlines()
            idx = content.find(old_text) if old_text else -1
            if idx >= 0:
                prefix = content[:idx]
                start_line_no = prefix.count("\n") + 1
                end_line_no = start_line_no + max(1, len(old_lines)) - 1
                all_lines = content.splitlines()
                before_start = max(1, start_line_no - 3)
                before = all_lines[before_start - 1:start_line_no - 1]
                after_end = min(len(all_lines), end_line_no + 3)
                after = all_lines[end_line_no:after_end]
                preview["edit_context"] = {
                    "before": _format_numbered_lines(before, before_start),
                    "old": _format_numbered_lines(old_lines or [""], start_line_no),
                    "new": _format_numbered_lines(new_lines or [""], start_line_no),
                    "after": _format_numbered_lines(after, end_line_no + 1),
                    "old_start_line": start_line_no,
                    "old_end_line": end_line_no,
                }
                mode_text = tr("tool_loop.preview_mode_all") if replace_all is True else tr("tool_loop.preview_mode_first")
                preview["summary"] = tr("tool_loop.preview_summary", file_path=file_path, count=len(replacements), start=start_line_no, end=end_line_no, mode=mode_text)
            else:
                preview["edit_context"] = {
                    "before": [],
                    "old": _format_numbered_lines(old_lines or [""], 1),
                    "new": _format_numbered_lines(new_lines or [""], 1),
                    "after": [],
                    "old_start_line": None,
                    "old_end_line": None,
                }
                preview["summary"] = tr("tool_loop.preview_old_not_found", count=len(replacements))
            if short_old_text_notice:
                preview["summary"] = f"{preview['summary']}{tr('tool_loop.preview_short_old_suffix')}"
        except Exception as exc:
            preview["summary"] = tr("tool_loop.preview_failed", error=exc)
        return preview

    if function_name in {"run_command", "terminal_input"}:
        preview["command"] = args.get("command")
        preview["summary"] = tr("tool_loop.preview_run_command", command=args.get('command') or '')
        return preview

    if function_name in {"create_file", "create_folder", "delete_file"}:
        preview["path"] = args.get("path")
        preview["summary"] = f"{function_name}: {args.get('path') or ''}"
        return preview

    if function_name == "rename_file":
        preview["old_path"] = args.get("old_path")
        preview["new_path"] = args.get("new_path")
        preview["summary"] = f"rename_file: {args.get('old_path') or ''} -> {args.get('new_path') or ''}"
        return preview

    if function_name == "write_file":
        content = str(args.get("content") or "")
        preview["file_path"] = args.get("file_path")
        preview["append"] = bool(args.get("append", False))
        preview["content_preview"] = content
        preview["content_length"] = len(content)
        preview["summary"] = f"write_file: {args.get('file_path') or ''} ({'append' if preview['append'] else 'overwrite'})"
        return preview

    return preview


def _is_permission_denied_result(result_data: Dict[str, Any]) -> bool:
    if not isinstance(result_data, dict):
        return False
    if result_data.get("success") is True:
        return False
    fragments: List[str] = []
    for key in ("error", "message", "output"):
        value = result_data.get(key)
        if isinstance(value, str) and value.strip():
            fragments.append(value.lower())
    joined = "\n".join(fragments)
    if not joined:
        return False
    markers = (
        "operation not permitted",
        "permission denied",
        "权限不足",
        "无权限",
        "不允许",
        "access denied",
        # 网络受限常见报错
        "could not resolve host",
        "nodename nor servname provided, or not known",
        "unknown host",
        "network is unreachable",
        "no route to host",
        "socket.gaierror",
    )
    return any(marker in joined for marker in markers)


def _is_file_permission_denied_result(result_data: Dict[str, Any]) -> bool:
    """是否为文件读写权限拒绝（区别于网络受限报错）。

    用于审批批准后重试仍失败的场景：方案一（2026-08-30）起可写沙箱读边界
    与只读同为白名单，批准不再放大读取；此时给模型追加「路径授权」引导。
    """
    if not isinstance(result_data, dict):
        return False
    if result_data.get("success") is True:
        return False
    fragments: List[str] = []
    for key in ("error", "message", "output"):
        value = result_data.get(key)
        if isinstance(value, str) and value.strip():
            fragments.append(value.lower())
    joined = "\n".join(fragments)
    if not joined:
        return False
    markers = (
        "operation not permitted",
        "permission denied",
        "access denied",
        "权限不足",
        "无权限",
        "不允许",
    )
    return any(marker in joined for marker in markers)


def _inject_runtime_mode_notice(
    *,
    web_terminal,
    messages: List[Dict[str, Any]],
    content: str,
    source: str = "notify",
    sender=None,
    conversation_id: Optional[str] = None,
) -> None:
    inject_runtime_user_message(
        web_terminal=web_terminal,
        messages=messages,
        text=content,
        source=source,
        sender=sender,
        conversation_id=conversation_id,
        inline=True,
    )


def _format_rejected_tool_text(reason: str) -> str:
    clean_reason = str(reason or "").strip() or tr("tool_loop.reason_not_provided")
    return tr("tool_loop.tool_call_rejected", reason=clean_reason)


async def _wait_for_tool_approval(*, approval_id: str, username: str, timeout_seconds: float = 3600.0) -> Dict[str, Any]:
    started = time.time()
    while True:
        row = tool_approval_manager.get(approval_id)
        if not row:
            return {"decision": "rejected", "code": "approval_missing", "reason": tr("tool_loop.approval_missing")}
        if row.get("username") != username:
            return {"decision": "rejected", "code": "approval_user_mismatch", "reason": tr("tool_loop.approval_user_mismatch")}
        status = row.get("status")
        if status in {"approved", "rejected"}:
            return {"decision": status, "item": row}
        if (time.time() - started) >= timeout_seconds:
            return {"decision": "rejected", "code": "approval_timeout", "reason": tr("tool_loop.approval_timeout")}
        await asyncio.sleep(0.2)


def _safe_parse_tool_arguments_for_question(web_terminal, tool_call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        function = tool_call.get("function") or {}
        if function.get("name") != "ask_user":
            return None
        raw = function.get("arguments") or "{}"
        if hasattr(web_terminal, 'api_client') and hasattr(web_terminal.api_client, '_safe_tool_arguments_parse'):
            success, arguments, _error_msg = web_terminal.api_client._safe_tool_arguments_parse(raw, "ask_user")
            if success and isinstance(arguments, dict):
                return arguments
            return None
        parsed = json.loads(raw) if str(raw).strip() else {}
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


async def _wait_for_user_questions(*, question_ids: List[str], username: str, timeout_seconds: float = 3600.0) -> Dict[str, Dict[str, Any]]:
    started = time.time()
    pending = {str(qid) for qid in question_ids if qid}
    answered: Dict[str, Dict[str, Any]] = {}
    while pending:
        for qid in list(pending):
            row = user_question_manager.get(qid)
            if not row:
                answered[qid] = {"status": "missing", "answer_text": tr("tool_loop.question_missing")}
                pending.remove(qid)
                continue
            if row.get("username") != username:
                answered[qid] = {"status": "forbidden", "answer_text": tr("tool_loop.question_user_mismatch")}
                pending.remove(qid)
                continue
            if row.get("status") == "answered":
                answered[qid] = {**row, "answer_text": format_user_question_answer(row)}
                pending.remove(qid)
        if not pending:
            break
        if (time.time() - started) >= timeout_seconds:
            for qid in list(pending):
                answered[qid] = {"status": "timeout", "answer_text": tr("tool_loop.question_timeout")}
                pending.remove(qid)
            break
        await asyncio.sleep(0.2)
    return answered


async def _wait_for_plan_approval(*, approval_id: str, username: str, timeout_seconds: float = 3600.0) -> Dict[str, Any]:
    """阻塞等待计划批准结果（轮询管理器，与 _wait_for_user_questions 同构）。"""
    started = time.time()
    while True:
        row = plan_approval_manager.get(approval_id)
        if not row:
            return {"status": "missing"}
        if row.get("username") != username:
            return {"status": "forbidden"}
        if row.get("status") in {"approved", "rejected"}:
            return row
        if (time.time() - started) >= timeout_seconds:
            return {"status": "timeout"}
        await asyncio.sleep(0.3)


async def _handle_workflow_tool(*, function_name: str, web_terminal, arguments, sender, workspace, messages, conversation_id: Optional[str]) -> str:
    """report_workflow_stage / choose_workflow_branch / deactivate_workflow 的工具循环特判 handler。

    推进矩阵需要 sender（审核进度事件）、workspace 与 conversation_id，
    与 submit_plan 一样在工具循环层处理而非 tools_execution 常规链。
    审核在工具内同步 await（handler 支持长阻塞，submit_plan 已验证该模式）。
    deactivate 摘牌后需要 sender 广播 {active: False} 快照（前端实时摘卡片）。
    推进成功后同步刷新 messages 里的工作流 system 段（当前位置不滞后）。
    """
    from server import workflow_flow

    args = arguments or {}
    try:
        if function_name == "report_workflow_stage":
            result = await workflow_flow.handle_stage_report(
                web_terminal=web_terminal,
                data_dir=workspace.data_dir,
                sender=sender,
                conversation_id=conversation_id,
                summary=str(args.get("summary") or ""),
            )
        elif function_name == "choose_workflow_branch":
            result = await workflow_flow.handle_branch_choice(
                web_terminal=web_terminal,
                data_dir=workspace.data_dir,
                sender=sender,
                conversation_id=conversation_id,
                target_node_id=str(args.get("target_node_id") or ""),
            )
        else:  # deactivate_workflow
            result = workflow_flow.deactivate_workflow(
                data_dir=workspace.data_dir,
                conversation_id=conversation_id,
                reason=str(args.get("reason") or ""),
                sender=sender,
            )
        if isinstance(result, dict) and result.get("success"):
            workflow_flow.refresh_workflow_system_segment(
                messages, data_dir=workspace.data_dir, conversation_id=conversation_id
            )
    except Exception as exc:
        # 柔性原则：任何工作流异常不得掐断智能体工作
        result = {"success": False, "error": tr("tool_loop.workflow_tool_error", error=exc)}
    return json.dumps(result, ensure_ascii=False)


async def _handle_submit_plan(*, web_terminal, arguments: Dict[str, Any], sender, username: str, conversation_id: Optional[str], tool_call_id: Optional[str]) -> str:
    """submit_plan 工具的计划批准流：弹窗展示计划文档 → 用户批准/拒绝 →
    批准则自动切换运行模式到 execute（联动恢复权限）并更新运行期模式基线。"""
    args = arguments or {}

    def _payload(**kwargs) -> str:
        return json.dumps(kwargs, ensure_ascii=False)

    # 1. 仅计划模式可调用（工具列表已按模式过滤，这里是兜底）
    try:
        current_work_mode = web_terminal.get_work_mode() if hasattr(web_terminal, "get_work_mode") else None
    except Exception:
        current_work_mode = None
    if current_work_mode != "plan":
        return _payload(success=False, status="not_in_plan_mode",
                        message=tr("tool_loop.submit_plan_not_in_plan_mode"))

    # 2. 校验计划文件路径并读取内容
    plan_file = str(args.get("plan_file") or "").strip()
    summary = str(args.get("summary") or "").strip()
    if not plan_file:
        return _payload(success=False, status="invalid_plan_file",
                        message=tr("tool_loop.submit_plan_missing_file"))
    try:
        valid, err, full_path = web_terminal.file_manager._validate_path(plan_file)
    except Exception as exc:
        valid, err, full_path = False, str(exc), None
    if not valid or not full_path:
        return _payload(success=False, status="invalid_plan_file",
                        message=tr("tool_loop.submit_plan_invalid_path", error=err or plan_file))
    try:
        project_root = Path(web_terminal.context_manager.project_path).resolve()
        plan_dir = (project_root / ".astrion" / "plan").resolve()
        resolved = Path(full_path).resolve()
        if resolved != plan_dir and plan_dir not in resolved.parents:
            return _payload(success=False, status="invalid_plan_file",
                            message=tr("tool_loop.submit_plan_wrong_dir"))
        if resolved.suffix.lower() != ".md":
            return _payload(success=False, status="invalid_plan_file",
                            message=tr("tool_loop.submit_plan_not_md"))
        if not resolved.is_file():
            return _payload(success=False, status="plan_file_missing",
                            message=tr("tool_loop.submit_plan_file_missing", plan_file=plan_file))
        plan_content = resolved.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return _payload(success=False, status="plan_file_error",
                        message=tr("tool_loop.submit_plan_read_failed", error=exc))
    if not plan_content.strip():
        return _payload(success=False, status="plan_file_empty",
                        message=tr("tool_loop.submit_plan_empty"))

    # 3. 创建批准请求并通知前端弹窗
    approval = plan_approval_manager.create_request(
        username=username,
        conversation_id=conversation_id,
        task_id=getattr(web_terminal, "task_id", None),
        tool_call_id=tool_call_id,
        plan_file=plan_file,
        plan_content=plan_content,
        summary=summary,
    )
    sender('update_action', {
        'preparing_id': tool_call_id,
        'status': 'awaiting_plan_approval',
        'result': {
            "success": False,
            "status": "awaiting_plan_approval",
            "approval_id": approval.get("approval_id"),
            "message": tr("tool_loop.awaiting_plan_approval")
        },
        'message': tr("tool_loop.awaiting_plan_approval"),
        'conversation_id': conversation_id
    })
    sender('plan_approval_required', {
        'approval': approval,
        'conversation_id': conversation_id,
    })

    # 4. 阻塞等待用户决定
    resolved = await _wait_for_plan_approval(approval_id=str(approval.get("approval_id") or ""), username=username)
    status = str(resolved.get("status") or "")
    comment = str(resolved.get("comment") or "").strip()
    sender('plan_approval_resolved', {
        'approval_id': approval.get("approval_id"),
        'decision': status,
        'comment': comment,
        'conversation_id': conversation_id,
    })

    # 5. 批准 → 切换到执行模式（恢复权限），并同步运行期模式基线避免重复通知
    if status == "approved":
        switch_note = ""
        try:
            result = web_terminal.switch_work_mode("execute")
            try:
                web_terminal.update_runtime_mode_baseline({
                    "work_mode": "execute",
                    "permission_mode": result.get("permission_mode") or "",
                })
            except Exception:
                pass
            try:
                from .extensions import socketio
                socketio.emit('status_update', web_terminal.get_status(), room=f"user_{username}")
            except Exception:
                pass
            switch_note = tr("tool_loop.plan_switch_note_ok")
        except Exception as exc:
            switch_note = tr("tool_loop.plan_switch_note_failed", error=exc)
        message = tr("tool_loop.plan_approved", switch_note=switch_note)
        if comment:
            message += tr("tool_loop.plan_approved_comment", comment=comment)
        return _payload(success=True, status="approved", message=message,
                        comment=comment, plan_file=plan_file)

    if status == "rejected":
        message = tr("tool_loop.plan_rejected")
        if comment:
            message += tr("tool_loop.plan_rejected_comment", comment=comment)
        else:
            message += tr("tool_loop.plan_rejected_no_comment")
        return _payload(success=False, status="rejected", message=message,
                        comment=comment, plan_file=plan_file)

    return _payload(success=False, status=status or "unknown",
                    message=tr("tool_loop.plan_no_decision"),
                    plan_file=plan_file)


async def execute_tool_calls(**kwargs):
    """_execute_tool_calls_impl 的守护包装：保证 _tool_loop_active 在任何退出路径都复位。

    历史教训（2026-08-12 平行时空事故）：标志的恢复逻辑散落在内层各 return 分支，
    并发交错或异常逃逸会把它永久卡在 True，导致完成通知轮询器死等、通知永远发不出。
    内层各 return 点的恢复仍然保留（尽早复位缩短轮询器等待），本包装做最终兜底。
    """
    web_terminal = kwargs.get("web_terminal")
    if web_terminal is None:
        return await _execute_tool_calls_impl(**kwargs)
    previous_tool_loop_active = getattr(web_terminal, "_tool_loop_active", False)
    try:
        return await _execute_tool_calls_impl(**kwargs)
    finally:
        web_terminal._tool_loop_active = previous_tool_loop_active


async def _execute_tool_calls_impl(*, web_terminal, tool_calls, sender, messages, client_sid: str, username: str, iteration: int, conversation_id: Optional[str], last_tool_call_time: float, process_sub_agent_updates, process_background_command_updates, process_multi_agent_master_messages=process_multi_agent_master_messages, get_stop_flag, clear_stop_flag, workspace=None):
    previous_tool_loop_active = getattr(web_terminal, "_tool_loop_active", False)
    web_terminal._tool_loop_active = True
    allowed_tool_names = set()
    try:
        defined_tools = web_terminal.define_tools() or []
        for tool in defined_tools:
            name = ((tool or {}).get("function") or {}).get("name")
            if isinstance(name, str) and name:
                allowed_tool_names.add(name)
    except Exception as exc:
        debug_log(f"构建工具白名单失败（降级继续）: {exc}")
    recent_tool_actions = list(getattr(web_terminal, "_recent_tool_actions", []) or [])

    user_question_results_by_tool_call_id: Dict[str, str] = {}
    user_question_id_by_tool_call_id: Dict[str, str] = {}
    ask_user_items: List[Dict[str, Any]] = []
    for idx, pending_tool_call in enumerate(tool_calls or []):
        function = (pending_tool_call or {}).get("function") or {}
        if function.get("name") != "ask_user":
            continue
        arguments = _safe_parse_tool_arguments_for_question(web_terminal, pending_tool_call)
        if not isinstance(arguments, dict):
            continue
        question_text = str(arguments.get("question") or "").strip()
        if not question_text:
            continue
        ask_user_items.append({
            "tool_call": pending_tool_call,
            "arguments": arguments,
            "order": idx,
        })

    if ask_user_items:
        batch_id = f"question_batch_{uuid.uuid4().hex}"
        batch_total = len(ask_user_items)
        created_questions: List[Dict[str, Any]] = []
        for batch_index, item in enumerate(ask_user_items, start=1):
            tool_call = item.get("tool_call") or {}
            arguments = item.get("arguments") or {}
            question = user_question_manager.create_question(
                username=username,
                conversation_id=conversation_id,
                task_id=getattr(web_terminal, "task_id", None),
                tool_call_id=tool_call.get("id"),
                question=arguments.get("question"),
                context=arguments.get("context"),
                options=arguments.get("options"),
                batch_id=batch_id,
                batch_index=batch_index,
                batch_total=batch_total,
            )
            created_questions.append(question)
            if tool_call.get("id"):
                user_question_id_by_tool_call_id[str(tool_call.get("id"))] = question.get("question_id")
            sender('update_action', {
                'preparing_id': tool_call.get("id"),
                'status': 'awaiting_user_answer',
                'result': {
                    "success": False,
                    "status": "awaiting_user_answer",
                    "question_id": question.get("question_id"),
                    "message": tr("tool_loop.awaiting_user_answer")
                },
                'message': tr("tool_loop.awaiting_user_answer"),
                'conversation_id': conversation_id
            })
        sender('user_questions_required', {
            'batch_id': batch_id,
            'questions': created_questions,
            'conversation_id': conversation_id,
        })
        wait_answers = await _wait_for_user_questions(
            question_ids=[str(q.get("question_id") or "") for q in created_questions],
            username=username,
        )
        for question in created_questions:
            qid = str(question.get("question_id") or "")
            answer_row = wait_answers.get(qid) or {}
            answer_text = str(answer_row.get("answer_text") or tr("tool_loop.user_no_answer")).strip() or tr("tool_loop.user_no_answer")
            tool_call_id = str(question.get("tool_call_id") or "")
            if tool_call_id:
                user_question_results_by_tool_call_id[tool_call_id] = answer_text
        sender('user_questions_resolved', {
            'batch_id': batch_id,
            'question_ids': [q.get("question_id") for q in created_questions],
            'conversation_id': conversation_id,
        })

    # 执行每个工具
    pending_runtime_mode_notices: List[str] = []
    # 工具结果附带的 system_message 先收集、整轮工具循环结束后统一注入，
    # 避免并行 tool_calls 中途插入 user 消息夹断 assistant.tool_calls 的 tool 序列。
    pending_tool_system_messages: List[Dict[str, Any]] = []
    last_completed_tool_call_id: Optional[str] = None
    deep_compression_pending = False
    for tool_call in tool_calls:
        # 检查停止标志
        client_stop_info = get_stop_flag(client_sid, username, include_user=False)
        if client_stop_info:
            stop_requested = client_stop_info.get('stop', False) if isinstance(client_stop_info, dict) else client_stop_info
            if stop_requested:
                debug_log("在工具调用过程中检测到停止状态")
                tool_call_id = tool_call.get("id")
                function_name = tool_call.get("function", {}).get("name")
                # 通知前端该工具已被取消，避免界面卡住
                sender('update_action', {
                    'preparing_id': tool_call_id,
                    'status': 'cancelled',
                    'result': {
                        "success": False,
                        "status": "cancelled",
                        "message": tr("tool_loop.cancelled_by_user"),
                        "tool": function_name
                    }
                })
                # 在消息列表中记录取消结果，防止重新加载时仍显示运行中
                if tool_call_id:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": function_name,
                        "content": tr("tool_loop.cancelled_by_user"),
                    })
                sender('task_stopped', {
                    'message': tr("tool_loop.cancelled_by_user"),
                    'reason': 'user_stop'
                })
                clear_stop_flag(client_sid, username)
                web_terminal._tool_loop_active = previous_tool_loop_active
                return {"stopped": True, "last_tool_call_time": last_tool_call_time}

        # 工具调用间隔控制
        current_time = time.time()
        if last_tool_call_time > 0:
            elapsed = current_time - last_tool_call_time
            if elapsed < TOOL_CALL_COOLDOWN:
                await asyncio.sleep(TOOL_CALL_COOLDOWN - elapsed)
        last_tool_call_time = time.time()

        function_name = tool_call["function"]["name"]
        arguments_str = tool_call["function"]["arguments"]
        tool_call_id = tool_call["id"]


        debug_log(f"准备解析JSON，工具: {function_name}, 参数长度: {len(arguments_str)}")
        debug_log(f"JSON参数前200字符: {arguments_str[:200]}")
        debug_log(f"JSON参数后200字符: {arguments_str[-200:]}")

        # 使用改进的参数解析方法
        if hasattr(web_terminal, 'api_client') and hasattr(web_terminal.api_client, '_safe_tool_arguments_parse'):
            success, arguments, error_msg = web_terminal.api_client._safe_tool_arguments_parse(arguments_str, function_name)
            if not success:
                debug_log(f"安全解析失败: {error_msg}")
                # 格式与前端 taskPolling/lifecycle.ts 识别正则联动，改动须同步前端
                error_text = tr("tool.param_parse_failed", error=error_msg)
                error_payload = {
                    "success": False,
                    "error": error_text,
                    "error_type": "parameter_format_error",
                    "tool_name": function_name,
                    "tool_call_id": tool_call_id,
                    "message": error_text
                }
                sender('error', {'message': error_text})
                sender('update_action', {
                    'preparing_id': tool_call_id,
                    'status': 'completed',
                    'result': error_payload,
                    'message': error_text
                })
                error_content = json.dumps(error_payload, ensure_ascii=False)
                web_terminal.context_manager.add_conversation(
                    "tool",
                    error_content,
                    tool_call_id=tool_call_id,
                    name=function_name
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "content": error_content
                })
                continue
            debug_log(f"使用安全解析成功，参数键: {list(arguments.keys())}")
        else:
            # 回退到带有基本修复逻辑的解析
            try:
                arguments = json.loads(arguments_str) if arguments_str.strip() else {}
                debug_log(f"直接JSON解析成功，参数键: {list(arguments.keys())}")
            except json.JSONDecodeError as e:
                debug_log(f"原始JSON解析失败: {e}")
                # 尝试基本的JSON修复
                repaired_str = arguments_str.strip()
                repair_attempts = []

                # 修复1: 未闭合字符串
                if repaired_str.count('"') % 2 == 1:
                    repaired_str += '"'
                    repair_attempts.append("添加闭合引号")

                # 修复2: 未闭合JSON对象
                if repaired_str.startswith('{') and not repaired_str.rstrip().endswith('}'):
                    repaired_str = repaired_str.rstrip() + '}'
                    repair_attempts.append("添加闭合括号")

                # 修复3: 截断的JSON（移除不完整的最后一个键值对）
                if not repair_attempts:  # 如果前面的修复都没用上
                    last_comma = repaired_str.rfind(',')
                    if last_comma > 0:
                        repaired_str = repaired_str[:last_comma] + '}'
                        repair_attempts.append("移除不完整的键值对")

                # 尝试解析修复后的JSON
                try:
                    arguments = json.loads(repaired_str)
                    debug_log(f"JSON修复成功: {', '.join(repair_attempts)}")
                    debug_log(f"修复后参数键: {list(arguments.keys())}")
                except json.JSONDecodeError as repair_error:
                    debug_log(f"JSON修复也失败: {repair_error}")
                    debug_log(f"修复尝试: {repair_attempts}")
                    debug_log(f"修复后内容前100字符: {repaired_str[:100]}")
                    # 格式与前端 taskPolling/lifecycle.ts 识别正则联动，改动须同步前端
                    error_text = tr("tool.param_parse_failed", error=e)
                    error_payload = {
                        "success": False,
                        "error": error_text,
                        "error_type": "parameter_format_error",
                        "tool_name": function_name,
                        "tool_call_id": tool_call_id,
                        "message": error_text
                    }
                    sender('error', {'message': error_text})
                    sender('update_action', {
                        'preparing_id': tool_call_id,
                        'status': 'completed',
                        'result': error_payload,
                        'message': error_text
                    })
                    error_content = json.dumps(error_payload, ensure_ascii=False)
                    web_terminal.context_manager.add_conversation(
                        "tool",
                        error_content,
                        tool_call_id=tool_call_id,
                        name=function_name
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": function_name,
                        "content": error_content
                    })
                    continue            

        # 严格校验：只允许执行当前回合明确下发给模型的工具。
        # 某些模型会“幻觉”调用未下发工具（如 view_image），必须在执行层拦截。
        if allowed_tool_names and function_name not in allowed_tool_names:
            if function_name == "view_image" and "vlm_analyze" in allowed_tool_names:
                if not arguments.get("prompt"):
                    arguments["prompt"] = "请详细分析这张图片的内容，包括关键文字、主体对象与场景信息。"
                debug_log(
                    "工具名自动纠正: view_image -> vlm_analyze (模型未获授权 view_image)"
                )
                function_name = "vlm_analyze"
            else:
                denied_message = tr("tool_loop.tool_not_allowed", tool=function_name)
                denied_payload = {
                    "success": False,
                    "status": "denied",
                    "code": "tool_not_allowed",
                    "tool": function_name,
                    "message": denied_message,
                }
                sender('update_action', {
                    'preparing_id': tool_call_id,
                    'status': 'completed',
                    'result': denied_payload,
                    'message': denied_message,
                    'conversation_id': conversation_id
                })
                denied_content = json.dumps(denied_payload, ensure_ascii=False)
                web_terminal.context_manager.add_conversation(
                    "tool",
                    denied_content,
                    tool_call_id=tool_call_id,
                    name=function_name
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "content": denied_content
                })
                continue

        debug_log(f"执行工具: {function_name} (ID: {tool_call_id})")
        previous_tool_actions = list(recent_tool_actions)
        recent_tool_actions.append({
            "tool_name": function_name,
            "arguments": arguments,
        })
        recent_tool_actions = recent_tool_actions[-3:]
        setattr(web_terminal, "_recent_tool_actions", list(recent_tool_actions))

        permission_eval = web_terminal.evaluate_tool_permission(function_name, arguments)
        if not permission_eval.get("allowed", True):
            denied_message = permission_eval.get("message") or tr("tool_loop.permission_denied_default")
            denied_payload = {
                "success": False,
                "status": "denied",
                "code": permission_eval.get("code") or "permission_denied",
                "tool": function_name,
                "mode": permission_eval.get("mode"),
                "message": denied_message,
            }
            sender('update_action', {
                'preparing_id': tool_call_id,
                'status': 'completed',
                'result': denied_payload,
                'message': denied_message,
                'conversation_id': conversation_id
            })
            denied_content = json.dumps(denied_payload, ensure_ascii=False)
            web_terminal.context_manager.add_conversation(
                "tool",
                denied_content,
                tool_call_id=tool_call_id,
                name=function_name
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": function_name,
                "content": denied_content
            })
            continue

        if permission_eval.get("requires_approval"):
            approval_preview = _build_tool_approval_preview(web_terminal, function_name, arguments)
            approval_item = tool_approval_manager.create_request(
                username=username,
                conversation_id=conversation_id,
                task_id=getattr(web_terminal, "task_id", None),
                tool_call_id=tool_call_id,
                tool_name=function_name,
                arguments=arguments,
                preview=approval_preview,
            )
            sender('tool_approval_required', {
                'approval': approval_item,
                'conversation_id': conversation_id,
            })
            sender('update_action', {
                'preparing_id': tool_call_id,
                'status': 'awaiting_approval',
                'result': {
                    "success": False,
                    "status": "awaiting_approval",
                    "approval_id": approval_item.get("approval_id"),
                    "message": tr("tool_loop.awaiting_approval")
                },
                'message': tr("tool_loop.awaiting_approval"),
                'conversation_id': conversation_id
            })
            wait_result = None
            permission_mode = str(permission_eval.get("mode") or "")
            if permission_mode == "auto_approval":
                approval_id = approval_item.get("approval_id")
                wait_result = await run_auto_approval(
                    web_terminal=web_terminal,
                    username=username,
                    approval_id=approval_id,
                    conversation_id=conversation_id,
                    recent_tool_actions=previous_tool_actions,
                    function_name=function_name,
                    arguments=arguments,
                    risk_markers=permission_eval.get("risk_markers") if isinstance(permission_eval, dict) else None,
                    sender=sender,
                )
            else:
                wait_result = await _wait_for_tool_approval(
                    approval_id=approval_item.get("approval_id"),
                    username=username,
                )
            sender('tool_approval_resolved', {
                'approval_id': approval_item.get("approval_id"),
                'decision': wait_result.get("decision"),
                'reason': ((wait_result.get("item") or {}).get("reason") or wait_result.get("reason")),
                'conversation_id': conversation_id,
            })
            if wait_result.get("decision") != "approved":
                reject_message = tr("tool_loop.rejected_by_user")
                if wait_result.get("code") == "approval_timeout":
                    reject_message = tr("tool_loop.rejected_timeout")
                reason = ((wait_result.get("item") or {}).get("reason") or wait_result.get("reason") or "").strip()
                if reason:
                    reject_message = f"{reject_message}：{reason}"
                reject_payload = {
                    "success": False,
                    "status": "rejected",
                    "code": "approval_rejected",
                    "tool": function_name,
                    "message": reject_message,
                    "approval_id": approval_item.get("approval_id"),
                }
                sender('update_action', {
                    'preparing_id': tool_call_id,
                    'status': 'completed',
                    'result': reject_payload,
                    'message': reject_message,
                    'conversation_id': conversation_id
                })
                reject_content = _format_rejected_tool_text(reason or reject_message)
                web_terminal.context_manager.add_conversation(
                    "tool",
                    reject_content,
                    tool_call_id=tool_call_id,
                    name=function_name
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "content": reject_content
                })
                if permission_mode != "auto_approval":
                    web_terminal._tool_loop_active = previous_tool_loop_active
                    return {
                        "stopped": False,
                        "approval_rejected": True,
                        "approval_message": reject_message,
                        "last_tool_call_time": last_tool_call_time
                    }
                continue

        # 发送工具开始事件
        tool_display_id = f"tool_{iteration}_{function_name}_{time.time()}"
        monitor_snapshot = None
        snapshot_path = None
        memory_snapshot_type = None
        if function_name in MONITOR_FILE_TOOLS:
            snapshot_path = resolve_monitor_path(arguments)
            monitor_snapshot = capture_monitor_snapshot(web_terminal.file_manager, snapshot_path, MONITOR_SNAPSHOT_CHAR_LIMIT, debug_log)
            if monitor_snapshot:
                cache_monitor_snapshot(tool_display_id, 'before', monitor_snapshot)
        elif function_name in MONITOR_MEMORY_TOOLS:
            memory_snapshot_type = (arguments.get('memory_type') or 'main').lower()
            before_entries = None
            try:
                before_entries = resolve_monitor_memory(web_terminal.memory_manager._read_entries(memory_snapshot_type), MONITOR_MEMORY_ENTRY_LIMIT)
            except Exception as exc:
                debug_log(f"[MonitorSnapshot] 读取记忆失败: {memory_snapshot_type} ({exc})")
            if before_entries is not None:
                monitor_snapshot = {
                    'memory_type': memory_snapshot_type,
                    'entries': before_entries
                }
                cache_monitor_snapshot(tool_display_id, 'before', monitor_snapshot)

        sender('tool_start', {
            'id': tool_display_id,
            'name': function_name,
            'arguments': arguments,
            'preparing_id': tool_call_id,
            'monitor_snapshot': monitor_snapshot,
            'conversation_id': conversation_id
        })
        debug_log(f"调用了工具: {function_name}")

        await asyncio.sleep(0.3)
        start_time = time.time()

        # 执行工具，同时监听停止标志
        debug_log(f"[停止检测] 开始执行工具: {function_name}")
        tool_result = None
        tool_cancelled = False
        tool_task = None
        check_count = 0

        if function_name == "ask_user" and str(tool_call_id) in user_question_results_by_tool_call_id:
            answer_text = user_question_results_by_tool_call_id.get(str(tool_call_id)) or tr("tool_loop.user_no_answer")
            qid = user_question_id_by_tool_call_id.get(str(tool_call_id))
            tool_result = json.dumps({
                "success": True,
                "status": "answered",
                "message": answer_text,
                "answer_text": answer_text,
                "question_id": qid,
            }, ensure_ascii=False)
        elif function_name == "submit_plan":
            tool_result = await _handle_submit_plan(
                web_terminal=web_terminal,
                arguments=arguments,
                sender=sender,
                username=username,
                conversation_id=conversation_id,
                tool_call_id=str(tool_call_id) if tool_call_id else None,
            )
        elif function_name in ("report_workflow_stage", "choose_workflow_branch", "deactivate_workflow"):
            tool_result = await _handle_workflow_tool(
                function_name=function_name,
                web_terminal=web_terminal,
                arguments=arguments,
                sender=sender,
                workspace=workspace,
                messages=messages,
                conversation_id=conversation_id,
            )
        else:
            tool_task = asyncio.create_task(web_terminal.handle_tool_call(function_name, arguments))

            # 在工具执行期间持续检查停止标志
            while not tool_task.done():
                await asyncio.sleep(0.1)  # 每100ms检查一次
                check_count += 1
                client_stop_info = get_stop_flag(client_sid, username, include_user=False)
                if client_stop_info:
                    stop_requested = client_stop_info.get('stop', False) if isinstance(client_stop_info, dict) else client_stop_info
                    if stop_requested:
                        debug_log(f"[停止检测] 工具执行过程中检测到停止请求(检查次数:{check_count})，立即取消工具")
                        tool_task.cancel()
                        tool_cancelled = True
                        break

        debug_log(f"[停止检测] 工具执行完成，cancelled={tool_cancelled}, 检查次数={check_count}")

        # 获取工具结果或处理取消
        if tool_cancelled:
            try:
                if tool_task is not None:
                    await tool_task
            except asyncio.CancelledError:
                debug_log("[停止检测] 工具任务已被取消(CancelledError)")
            except Exception as e:
                debug_log(f"[停止检测] 工具任务取消时发生异常: {e}")

            # 返回取消消息
            tool_result = json.dumps({
                "success": False,
                "status": "cancelled",
                "message": tr("tool_loop.cancelled_by_user")
            }, ensure_ascii=False)

            debug_log("[停止检测] 发送取消通知到前端")
            # 通知前端工具被取消
            sender('update_action', {
                'preparing_id': tool_call_id,
                'status': 'cancelled',
                'result': {
                    "success": False,
                    "status": "cancelled",
                    "message": tr("tool_loop.cancelled_by_user"),
                    "tool": function_name
                }
            })

            # 记录取消结果到消息历史
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": function_name,
                "content": tr("tool_loop.cancelled_by_user"),
            })

            # 保存取消结果
            web_terminal.context_manager.add_conversation(
                "tool",
                tr("tool_loop.cancelled_by_user"),
                tool_call_id=tool_call_id,
                name=function_name,
                metadata={"status": "cancelled"}
            )
            debug_log("[停止检测] 取消结果已保存到对话历史")

            # 发送停止事件并清除标志
            sender('task_stopped', {
                'message': tr("tool_loop.cancelled_by_user"),
                'reason': 'user_stop'
            })
            clear_stop_flag(client_sid, username)
            debug_log("[停止检测] 返回stopped=True")
            web_terminal._tool_loop_active = previous_tool_loop_active
            return {"stopped": True, "last_tool_call_time": last_tool_call_time}
        else:
            if tool_result is None and tool_task is not None:
                tool_result = await tool_task
            if tool_result is None:
                tool_result = json.dumps({"success": False, "error": tr("tool_loop.tool_no_result")}, ensure_ascii=False)
            debug_log(f"工具结果: {tool_result[:200]}...")

        execution_time = time.time() - start_time
        if execution_time < 1.5:
            await asyncio.sleep(1.5 - execution_time)

        # 更新工具状态
        result_data = {}
        try:
            result_data = json.loads(tool_result)
        except:
            result_data = {'output': tool_result}

        # 批准模式下 run_command 采用“先只读执行，触发权限拒绝后再审批并单次可写重试”。
        if (
            function_name == "run_command"
            and permission_eval.get("mode") in {"approval", "auto_approval"}
            and not bool(arguments.get("_approval_write_granted", False))
            and _is_permission_denied_result(result_data)
        ):
            approval_preview = _build_tool_approval_preview(web_terminal, function_name, arguments)
            approval_item = tool_approval_manager.create_request(
                username=username,
                conversation_id=conversation_id,
                task_id=getattr(web_terminal, "task_id", None),
                tool_call_id=tool_call_id,
                tool_name=function_name,
                arguments=arguments,
                preview=approval_preview,
            )
            sender('tool_approval_required', {
                'approval': approval_item,
                'conversation_id': conversation_id,
            })
            sender('update_action', {
                'preparing_id': tool_call_id,
                'status': 'awaiting_approval',
                'result': {
                    "success": False,
                    "status": "awaiting_approval",
                    "approval_id": approval_item.get("approval_id"),
                    "message": tr("tool_loop.awaiting_approval_retry")
                },
                'message': tr("tool_loop.awaiting_approval_retry_short"),
                'conversation_id': conversation_id
            })
            wait_result = None
            permission_mode = str(permission_eval.get("mode") or "")
            if permission_mode == "auto_approval":
                approval_id = approval_item.get("approval_id")
                wait_result = await run_auto_approval(
                    web_terminal=web_terminal,
                    username=username,
                    approval_id=approval_id,
                    conversation_id=conversation_id,
                    recent_tool_actions=previous_tool_actions,
                    function_name=function_name,
                    arguments=arguments,
                    risk_markers=permission_eval.get("risk_markers") if isinstance(permission_eval, dict) else None,
                    sender=sender,
                )
            else:
                wait_result = await _wait_for_tool_approval(
                    approval_id=approval_item.get("approval_id"),
                    username=username,
                )
            sender('tool_approval_resolved', {
                'approval_id': approval_item.get("approval_id"),
                'decision': wait_result.get("decision"),
                'reason': ((wait_result.get("item") or {}).get("reason") or wait_result.get("reason")),
                'conversation_id': conversation_id,
            })
            if wait_result.get("decision") != "approved":
                reject_message = tr("tool_loop.rejected_by_user")
                if wait_result.get("code") == "approval_timeout":
                    reject_message = tr("tool_loop.rejected_timeout")
                reason = ((wait_result.get("item") or {}).get("reason") or wait_result.get("reason") or "").strip()
                if reason:
                    reject_message = f"{reject_message}：{reason}"
                reject_payload = {
                    "success": False,
                    "status": "rejected",
                    "code": "approval_rejected",
                    "tool": function_name,
                    "message": reject_message,
                    "approval_id": approval_item.get("approval_id"),
                }
                sender('update_action', {
                    'preparing_id': tool_call_id,
                    'status': 'completed',
                    'result': reject_payload,
                    'message': reject_message,
                    'conversation_id': conversation_id
                })
                reject_content = _format_rejected_tool_text(reason or reject_message)
                web_terminal.context_manager.add_conversation(
                    "tool",
                    reject_content,
                    tool_call_id=tool_call_id,
                    name=function_name
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": function_name,
                    "content": reject_content
                })
                if permission_mode == "approval":
                    web_terminal._tool_loop_active = previous_tool_loop_active
                    return {
                        "stopped": False,
                        "approval_rejected": True,
                        "approval_message": reject_message,
                        "last_tool_call_time": last_tool_call_time
                    }
                continue

            retry_arguments = dict(arguments or {})
            retry_arguments["_approval_write_granted"] = True
            retry_arguments["_approval_network_granted"] = True
            retry_tool_result = await web_terminal.handle_tool_call(function_name, retry_arguments)
            try:
                result_data = json.loads(retry_tool_result)
                tool_result = retry_tool_result
            except Exception:
                result_data = {"output": retry_tool_result}
                tool_result = retry_tool_result
            # 方案一（2026-08-30）：批准只授予「本次工作区内写权限」，可写沙箱读边界
            # 与只读同为白名单（不再全局可读）。重试后仍为文件权限拒绝 ⇒ 大概率是
            # 读取了授权范围外的路径——提示模型引导用户走「路径授权」，审批不放大读取。
            if _is_file_permission_denied_result(result_data):
                if isinstance(result_data, dict):
                    result_data["read_scope_hint"] = tr("tool_loop.retry_still_denied_read_scope")
                    tool_result = json.dumps(result_data, ensure_ascii=False)

        tool_failed = detect_tool_failure(result_data)

        action_status = 'completed'
        action_message = None
        awaiting_flag = False

        if function_name in {"write_file", "edit_file"}:
            diff_path = result_data.get("path") or arguments.get("file_path")
            summary = result_data.get("summary") or result_data.get("message")
            if summary:
                action_message = summary
            debug_log(f"{function_name} 执行完成: {summary or '无摘要'}")

        if function_name == "sleep":
            try:
                if isinstance(result_data, dict) and result_data.get("mode") == "wait_sub_agent_ids":
                    waited_task_ids = result_data.get("waited_task_ids") or []
                    if not hasattr(web_terminal, "_announced_sub_agent_tasks"):
                        web_terminal._announced_sub_agent_tasks = set()
                    for tid in waited_task_ids:
                        if tid:
                            web_terminal._announced_sub_agent_tasks.add(str(tid))
            except Exception:
                pass
        monitor_snapshot_after = None
        if function_name in MONITOR_FILE_TOOLS:
            result_path = None
            if isinstance(result_data, dict):
                result_path = resolve_monitor_path(result_data)
                if not result_path:
                    candidate_path = result_data.get('path')
                    if isinstance(candidate_path, str) and candidate_path.strip():
                        result_path = candidate_path.strip()
            if not result_path:
                result_path = resolve_monitor_path(arguments, snapshot_path) or snapshot_path
            monitor_snapshot_after = capture_monitor_snapshot(web_terminal.file_manager, result_path, MONITOR_SNAPSHOT_CHAR_LIMIT, debug_log)
        elif function_name in MONITOR_MEMORY_TOOLS:
            memory_after_type = str(
                arguments.get('memory_type')
                or (isinstance(result_data, dict) and result_data.get('memory_type'))
                or memory_snapshot_type
                or 'main'
            ).lower()
            after_entries = None
            try:
                after_entries = resolve_monitor_memory(web_terminal.memory_manager._read_entries(memory_after_type), MONITOR_MEMORY_ENTRY_LIMIT)
            except Exception as exc:
                debug_log(f"[MonitorSnapshot] 读取记忆失败(after): {memory_after_type} ({exc})")
            if after_entries is not None:
                monitor_snapshot_after = {
                    'memory_type': memory_after_type,
                    'entries': after_entries
                }

        mcp_parsed = None
        update_result_data = result_data
        if isinstance(function_name, str) and function_name.startswith("mcp__") and isinstance(result_data, dict):
            try:
                mcp_parsed = extract_mcp_content_for_context(result_data)
                update_result_data = mcp_parsed.get("sanitized_payload") or result_data
            except Exception:
                mcp_parsed = None
                update_result_data = result_data

        update_payload = {
            'id': tool_display_id,
            'status': action_status,
            'result': update_result_data,
            'preparing_id': tool_call_id,
            'conversation_id': conversation_id
        }
        if action_message:
            update_payload['message'] = action_message
        if awaiting_flag:
            update_payload['awaiting_content'] = True
        if monitor_snapshot_after:
            update_payload['monitor_snapshot_after'] = monitor_snapshot_after
            cache_monitor_snapshot(tool_display_id, 'after', monitor_snapshot_after)

        sender('update_action', update_payload)

        if function_name in ['create_file', 'delete_file', 'rename_file', 'create_folder']:
            if not web_terminal.context_manager._is_host_mode_without_safety():
                structure = web_terminal.context_manager.get_project_structure()
                sender('file_tree_update', structure)

        # ===== 增量保存：立即保存工具结果 =====
        metadata_payload = None
        tool_images = None
        tool_videos = None
        tool_media_refs = None
        if isinstance(result_data, dict):
            if isinstance(function_name, str) and function_name.startswith("mcp__"):
                if not isinstance(mcp_parsed, dict):
                    mcp_parsed = extract_mcp_content_for_context(result_data)
                tool_result_content = (
                    mcp_parsed.get("text")
                    or format_tool_result_for_context(function_name, result_data, tool_result)
                )
                mcp_media_items = mcp_parsed.get("media_items") or []
                if mcp_media_items:
                    tool_media_refs = []
                    for item in mcp_media_items:
                        if not isinstance(item, dict):
                            continue
                        tool_media_refs.append(
                            {
                                "kind": item.get("kind"),
                                "mime_type": item.get("mime_type"),
                                "data_base64": item.get("data_base64"),
                                "source": "mcp_content",
                                "item_type": item.get("item_type"),
                                "label": item.get("label"),
                                "name": item.get("name"),
                                "title": item.get("title"),
                                "uri": item.get("uri"),
                                "url": item.get("url"),
                                "index": item.get("index"),
                            }
                        )
                metadata_payload = {
                    "tool_payload": mcp_parsed.get("sanitized_payload") or result_data
                }
            else:
                # 特殊处理 web_search：保留可供前端渲染的精简结构，以便历史记录复现搜索结果
                if function_name == "web_search":
                    try:
                        tool_result_content = json.dumps(compact_web_search_result(result_data), ensure_ascii=False)
                    except Exception:
                        tool_result_content = tool_result
                else:
                    tool_result_content = format_tool_result_for_context(function_name, result_data, tool_result)
                metadata_payload = {"tool_payload": result_data}
        else:
            tool_result_content = tool_result
        tool_message_content = tool_result_content

        # view_image: 将图片直接附加到 tool 结果中（不再插入 user 消息）
        if function_name == "view_image" and getattr(web_terminal, "pending_image_view", None):
            inj = web_terminal.pending_image_view
            web_terminal.pending_image_view = None
            if (
                not tool_failed
                and isinstance(result_data, dict)
                and result_data.get("success") is not False
            ):
                img_path = inj.get("path") if isinstance(inj, dict) else None
                if img_path:
                    tool_images = [img_path]
                    if metadata_payload is None:
                        metadata_payload = {}
                    metadata_payload["tool_image_path"] = img_path
                    sender('system_message', {
                        'content': tr("tool_loop.image_path_recorded", path=img_path)
                    })

        # view_video: 将视频直接附加到 tool 结果中（不再插入 user 消息）
        if function_name == "view_video" and getattr(web_terminal, "pending_video_view", None):
            inj = web_terminal.pending_video_view
            web_terminal.pending_video_view = None
            if (
                not tool_failed
                and isinstance(result_data, dict)
                and result_data.get("success") is not False
            ):
                video_path = inj.get("path") if isinstance(inj, dict) else None
                if video_path:
                    tool_videos = [video_path]
                    if metadata_payload is None:
                        metadata_payload = {}
                    metadata_payload["tool_video_path"] = video_path
                    sender('system_message', {
                        'content': tr("tool_loop.video_path_recorded", path=video_path)
                    })

        # 立即保存工具结果
        saved_tool_message = web_terminal.context_manager.add_conversation(
            "tool",
            tool_result_content,
            tool_call_id=tool_call_id,
            name=function_name,
            metadata=metadata_payload,
            images=tool_images,
            videos=tool_videos,
            media_refs=tool_media_refs,
        )
        saved_media_refs = []
        if isinstance(saved_tool_message, dict):
            saved_media_refs = saved_tool_message.get("media_refs") or []

        # 将工具结果即时组装为多模态消息，确保同一轮 tool loop 的下一次模型请求能真正看到媒体
        if tool_images or tool_videos or saved_media_refs:
            try:
                tool_message_content = web_terminal.context_manager._build_content_with_images(
                    str(tool_result_content or ""),
                    tool_images or [],
                    tool_videos or [],
                    media_refs=saved_media_refs,
                )
            except Exception:
                tool_message_content = tool_result_content

        # 添加到消息历史（用于 API 继续对话）。
        # 必须在任何可能插入 user 消息/触发深层压缩总结之前完成，避免 assistant.tool_calls
        # 与对应 tool 结果之间夹入 user，尤其是同一轮并行 tool_calls 尚未全部返回时。
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": function_name,
            "content": tool_message_content
        })
        last_completed_tool_call_id = tool_call_id

        try:
            workspace_data_dir = getattr(workspace, "data_dir", None) if workspace else None
            personal_config = load_personalization_config(workspace_data_dir) if workspace_data_dir else {}
        except Exception:
            personal_config = {}
        compression_settings = resolve_context_compression_settings(personal_config)
        auto_shallow_enabled = bool(personal_config.get("auto_shallow_compress_enabled", False))
        auto_deep_enabled = bool(personal_config.get("auto_deep_compress_enabled", False))
        compressed_count = 0
        try:
            compressed_count = int(
                web_terminal.context_manager.on_tool_call_finished(
                    function_name,
                    enable_shallow=auto_shallow_enabled,
                    shallow_trigger_tokens=compression_settings["shallow_trigger_tokens"],
                    deep_trigger_tokens=compression_settings["deep_trigger_tokens"],
                    shallow_batch_size=compression_settings["shallow_max_replace_per_round"],
                    shallow_keep_recent_tools=compression_settings["shallow_keep_recent_tools"],
                    shallow_trigger_tool_calls_interval=compression_settings["shallow_trigger_tool_calls_interval"],
                    shallow_keep_user_turn_tools=compression_settings.get("shallow_keep_user_turn_tools", 3),
                ) or 0
            )
        except Exception as exc:
            debug_log(f"[ContextCompression] 工具后浅压缩检测失败: {exc}")
        if compressed_count > 0:
            sender('shallow_compression', {
                "conversation_id": conversation_id,
                "compressed_count": compressed_count,
                "keep_recent_tools": compression_settings["shallow_keep_recent_tools"],
                "keep_user_turn_tools": compression_settings.get("shallow_keep_user_turn_tools", 3),
            })
        # 关键修复：同一轮工具循环里，API 继续调用使用的是本地 messages（不是每次重建上下文），
        # 因此需要把已打标的旧 tool 结果同步替换为占位符，避免日志看起来“弹窗触发但请求未替换”。
        if auto_shallow_enabled and messages:
            try:
                marked_keys = set()
                for item in (web_terminal.context_manager.conversation_history or []):
                    if not isinstance(item, dict) or item.get("role") != "tool":
                        continue
                    item_meta = item.get("metadata") or {}
                    if not item_meta.get("auto_shallow_compacted"):
                        continue
                    marked_keys.add((str(item.get("tool_call_id") or ""), str(item.get("name") or "")))

                if marked_keys:
                    replaced_in_loop = 0
                    for msg in messages:
                        if not isinstance(msg, dict) or msg.get("role") != "tool":
                            continue
                        key = (str(msg.get("tool_call_id") or ""), str(msg.get("name") or ""))
                        if key in marked_keys and msg.get("content") != AUTO_SHALLOW_PLACEHOLDER:
                            msg["content"] = AUTO_SHALLOW_PLACEHOLDER
                            replaced_in_loop += 1
                    if replaced_in_loop > 0:
                        debug_log(f"[ContextCompression] 同步替换本轮 messages 中已压缩 tool 结果: {replaced_in_loop}")
            except Exception as exc:
                debug_log(f"[ContextCompression] 同步替换本轮 messages 失败: {exc}")
        debug_log(f"💾 增量保存：工具结果 {function_name}")
        system_message = result_data.get("system_message") if isinstance(result_data, dict) else None
        if system_message:
            # 不在此立即注入：若本轮是并行 tool_calls，其余 tool 结果尚未写入，
            # 此时插入 user 消息会夹断 assistant.tool_calls 的 tool 序列，触发 API 400
            # （tool_calls must be followed by tool messages）。统一延迟到循环结束后注入。
            pending_tool_system_messages.append({
                "text": system_message,
                "task_id": result_data.get("task_id"),
            })

        # 自动深层压缩（工具调用后触发）
        current_context_tokens = web_terminal.context_manager.get_current_context_tokens(conversation_id)
        if (
            auto_deep_enabled
            and current_context_tokens > compression_settings["deep_trigger_tokens"]
            and not web_terminal.context_manager.is_compression_in_progress()
        ):
            # 只标记，不能在本工具刚结束时立即压缩。若本轮是并行 tool_calls，
            # 立即调用总结模型会在 assistant.tool_calls 与尚未补齐的 tool 结果之间插入 user prompt，
            # 触发 OpenAI 兼容接口的 tool message 顺序校验错误。
            deep_compression_pending = True

        if not pending_runtime_mode_notices:
            try:
                if hasattr(web_terminal, "apply_pending_runtime_mode_changes"):
                    pending_runtime_mode_notices = list(web_terminal.apply_pending_runtime_mode_changes() or [])
            except Exception as exc:
                debug_log(f"[RuntimeMode] 应用挂起模式变更失败: {exc}")

        await asyncio.sleep(0.2)

    # 工具结果附带的 system_message：必须等待同一轮全部 tool_call 的 tool 消息
    # 都已写入 messages/历史后再注入，避免夹断 assistant.tool_calls 的 tool 序列。
    # 注意放在深层压缩分支之前，防止压缩提前 return 导致通知丢失。
    for pending_system_message in pending_tool_system_messages:
        inject_runtime_user_message(
            web_terminal=web_terminal,
            messages=messages,
            text=pending_system_message["text"],
            source="sub_agent",
            sender=sender,
            conversation_id=conversation_id,
            after_tool_call_id=last_completed_tool_call_id,
            inline=False,
            extra_metadata={"task_id": pending_system_message.get("task_id")},
        )

    # 自动深层压缩必须等待同一轮全部 tool_call 的 tool 消息都已写入 messages/历史后再触发。
    if deep_compression_pending and not web_terminal.context_manager.is_compression_in_progress():
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
        web_terminal._tool_loop_active = previous_tool_loop_active
        return {
            "stopped": False,
            "deep_compressed": True,
            "deep_result": deep_result,
            "last_tool_call_time": last_tool_call_time
        }

    # 子智能体/后台指令完成通知：必须等待同一轮全部 tool_call 都完成后再注入，
    # 避免在 assistant.tool_calls 与未完成的 tool 结果之间插入 system 消息导致 API 报错。
    if last_completed_tool_call_id:
        debug_log(
            f"[SubAgent] after all tools finished -> poll updates inline after_tool_call_id={last_completed_tool_call_id}"
        )
        await process_sub_agent_updates(
            messages=messages,
            inline=True,
            after_tool_call_id=last_completed_tool_call_id,
            web_terminal=web_terminal,
            sender=sender,
            debug_log=debug_log,
        )
        debug_log(
            "[BgCmdDebug] after all tools finished -> poll background command updates "
            f"inline after_tool_call_id={last_completed_tool_call_id}"
        )
        await process_background_command_updates(
            messages=messages,
            inline=True,
            after_tool_call_id=last_completed_tool_call_id,
            web_terminal=web_terminal,
            sender=sender,
            debug_log=debug_log,
        )
        await process_multi_agent_master_messages(
            messages=messages,
            inline=True,
            after_tool_call_id=last_completed_tool_call_id,
            web_terminal=web_terminal,
            sender=sender,
            debug_log=debug_log,
        )
        await process_workflow_updates(
            web_terminal=web_terminal,
            messages=messages,
            sender=sender,
            inline=True,
            after_tool_call_id=last_completed_tool_call_id,
            debug_log=debug_log,
        )

    # 运行期模式通知：必须等待同一轮全部 tool_call 都完成后再注入，
    # 避免在 assistant.tool_calls 与对应 tool 消息之间插入 user 消息导致 API 报错。
    if pending_runtime_mode_notices:
        for notice in pending_runtime_mode_notices:
            if isinstance(notice, dict):
                _text = str(notice.get("text") or "").strip()
                _source = str(notice.get("source") or "notify").strip() or "notify"
                _kind = str(notice.get("kind") or "").strip()
                _mode = str(notice.get("mode") or "").strip()
            else:
                _text = str(notice or "").strip()
                _source = "notify"
                _kind = ""
                _mode = ""
            if not _text:
                continue
            _inject_runtime_mode_notice(
                web_terminal=web_terminal,
                messages=messages,
                content=_text,
                source=_source,
                sender=sender,
                conversation_id=conversation_id,
            )
            # 通知真正注入对话后同步更新基线，避免下一条 user 消息重复补发。
            if _kind and _mode and hasattr(web_terminal, "update_runtime_mode_baseline"):
                try:
                    web_terminal.update_runtime_mode_baseline({_kind: _mode})
                except Exception:
                    pass

    # 运行期“引导对话”：需要等待同一轮全部工具执行结束后再注入，
    # 避免一轮内并行/多工具调用时过早插入。
    try:
        from .tasks import task_manager

        runtime_guidance_items = task_manager.consume_runtime_guidance_for_injection(
            username=username, task_id=client_sid
        )
    except Exception as exc:
        runtime_guidance_items = []
        debug_log(f"[RuntimeGuidance] 读取引导队列失败: {exc}")

    if runtime_guidance_items:
        injected_count = 0
        for raw_item in runtime_guidance_items:
            runtime_guidance_source = "guidance"
            runtime_guidance_files: List[str] = []
            if isinstance(raw_item, dict):
                runtime_guidance_text = str(raw_item.get("text") or "").strip()
                runtime_guidance_source = (
                    str(raw_item.get("source") or "guidance").strip().lower()
                    or "guidance"
                )
                raw_files = raw_item.get("files")
                if isinstance(raw_files, list):
                    runtime_guidance_files = [
                        str(p).strip()
                        for p in raw_files
                        if isinstance(p, str) and str(p).strip()
                    ][:9]
            else:
                runtime_guidance_text = str(raw_item or "").strip()
            if not runtime_guidance_text:
                continue
            inject_runtime_user_message(
                web_terminal=web_terminal,
                messages=messages,
                text=runtime_guidance_text,
                source=runtime_guidance_source,
                sender=sender,
                conversation_id=conversation_id,
                inline=True,
                extra_metadata={"files": runtime_guidance_files} if runtime_guidance_files else None,
            )
            # 携带文件的引导：同步注入一条 hidden 文件路径通知，让模型感知文件位置
            if runtime_guidance_files:
                file_notice_lines = ["本次消息附带的文件路径："]
                for _path in runtime_guidance_files:
                    file_notice_lines.append(f"- `{_path}`")
                inject_runtime_user_message(
                    web_terminal=web_terminal,
                    messages=messages,
                    text="\n".join(file_notice_lines),
                    source="file_paths",
                    sender=sender,
                    conversation_id=conversation_id,
                    inline=True,
                )
            injected_count += 1
        if injected_count:
            debug_log(
                "[RuntimeGuidance] 已在工具结果批次结束后批量注入引导/通知 "
                f"task_id={client_sid} count={injected_count}"
            )

    web_terminal._tool_loop_active = previous_tool_loop_active
    return {"stopped": False, "last_tool_call_time": last_tool_call_time}
