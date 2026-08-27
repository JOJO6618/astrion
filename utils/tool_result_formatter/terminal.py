from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from utils.tool_result_formatter.common import (
    _format_failure, _preview_text, _summarize_output_block, _summarize_todo_tasks
)

def _format_terminal_session(result_data: Dict[str, Any]) -> str:
    action = result_data.get("action") or result_data.get("terminal_action") or "未知操作"
    tag = f"terminal_session[{action}]"
    if not result_data.get("success"):
        return _format_failure(tag, result_data)
    if action == "open":
        return (
            f"终端 {result_data.get('session')} 已打开，工作目录 {result_data.get('working_dir')}，"
            f"当前活动会话: {result_data.get('session')}（共 {result_data.get('total_sessions')} 个）"
        )
    if action == "close":
        new_active = result_data.get("new_active") or "无"
        remaining = result_data.get("remaining_sessions") or []
        return (
            f"终端 {result_data.get('session')} 已关闭，新的活动会话: {new_active}。"
            f"剩余会话: {', '.join(remaining) if remaining else '无'}"
        )
    if action == "reset":
        session = result_data.get("session") or "未知会话"
        working_dir = result_data.get("working_dir") or "未知目录"
        return f"终端 {session} 已重置，工作目录 {working_dir}。"
    if action == "list":
        sessions = result_data.get("sessions") or []
        total = result_data.get("total", len(sessions))
        max_allowed = result_data.get("max_allowed")
        active = result_data.get("active") or "无"
        header = f"共有 {total}/{max_allowed} 个终端会话，活动会话: {active}"
        session_lines = []
        for session in sessions:
            name = session.get("session_name") or session.get("name") or "未命名"
            state = "运行中" if session.get("is_running") else "已停止"
            marker = "⭐" if session.get("is_active") else " "
            working_dir = session.get("working_dir") or "未知目录"
            session_lines.append(f"{marker} {name} | {state} | {working_dir}")
        return "\n".join([header] + session_lines) if session_lines else header
    return result_data.get("message") or f"{tag} 操作已完成。"

def _plain_command_output(result_data: Dict[str, Any]) -> str:
    """生成纯文本输出，按需要加状态前缀。"""
    output = result_data.get("output") or ""
    status = (result_data.get("status") or "").lower()
    timeout = result_data.get("timeout")
    if timeout is None:
        timeout = result_data.get("output_wait")
    return_code = result_data.get("return_code")
    truncated = result_data.get("truncated")
    error = result_data.get("error")
    message = result_data.get("message")

    prefixes = []
    partial_output_note = None
    partial_no_output_note = None
    if status in {"timeout"}:
        never_timeout = (
            (isinstance(timeout, str) and timeout.lower() == "never")
            or result_data.get("never_timeout")
        )
        if never_timeout:
            elapsed_ms = result_data.get("elapsed_ms")
            secs = None
            if isinstance(elapsed_ms, (int, float)) and elapsed_ms > 0:
                secs = max(1, int(round(elapsed_ms / 1000)))
            if secs:
                prefixes.append(f"[partial_output ~{secs}s]")
                partial_output_note = f"已返回约{secs}秒内输出，命令可能仍在运行"
                partial_no_output_note = f"已等待约{secs}秒未捕获输出，命令可能仍在运行"
            else:
                prefixes.append("[partial_output]")
                partial_output_note = "已返回当前输出，命令可能仍在运行"
                partial_no_output_note = "未捕获输出，命令可能仍在运行"
        else:
            timeout_seconds = None
            if isinstance(timeout, (int, float)) and timeout > 0:
                timeout_seconds = int(timeout)
            elif isinstance(timeout, str):
                stripped = timeout.strip()
                if stripped.isdigit():
                    timeout_seconds = int(stripped)
                else:
                    digits = "".join(ch for ch in stripped if ch.isdigit())
                    if digits:
                        timeout_seconds = int(digits)

            if timeout_seconds and timeout_seconds >= 60:
                prefixes.append(f"[partial_output ~{timeout_seconds}s]")
                partial_output_note = f"已等待约{timeout_seconds}秒，命令可能仍在运行"
                partial_no_output_note = f"已等待约{timeout_seconds}秒未捕获输出，命令可能仍在运行"
    elif status in {"killed"}:
        prefixes.append("[killed]")
    elif status in {"awaiting_input"}:
        prefixes.append("[awaiting_input]")
    elif status in {"no_output"} and not output:
        prefixes.append("[no_output]")
    elif status in {"error"} and return_code is not None:
        prefixes.append(f"[error rc={return_code}]")
    elif status in {"error"}:
        prefixes.append("[error]")

    if truncated:
        prefixes.append("[truncated]")

    # 如果执行失败且没有输出，优先显示错误信息
    if not result_data.get("success") and not output:
        err_text = partial_no_output_note or error or message
        if err_text:
            prefix_text = "".join(prefixes) if prefixes else "[error]"
            return f"{prefix_text} {err_text}" if prefix_text else err_text
        # 没有错误文本则仍走后面的输出逻辑（可能显示 no_output）

    prefix_text = "".join(prefixes)
    if prefix_text and output:
        if partial_output_note:
            prefix_text = f"{prefix_text} {partial_output_note}"
        return f"{prefix_text}\n{output}"
    if prefix_text:
        if partial_no_output_note:
            prefix_text = f"{prefix_text} {partial_no_output_note}"
        return prefix_text
    if not output:
        return "[no_output]"
    return output

def _format_terminal_input(result_data: Dict[str, Any]) -> str:
    text = _plain_command_output(result_data)
    timeout_hint = result_data.get("timeout_hint")
    if timeout_hint == "suggest_adjust_timeout":
        suggestion = "请根据指令和输出结果判断是否需要提高等待输出时长或修改指令输入"
        return f"{text}\n{suggestion}" if text else suggestion
    if timeout_hint == "suggest_never_timeout":
        suggestion = "请考虑设置 timeout 为 never，让终端持续执行该命令（⚠️注意 在该命令彻底执行完成前该终端会被占用，处于不可输入的状态）"
        return f"{text}\n{suggestion}" if text else suggestion
    return text

def _format_sleep(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("sleep", result_data)
    mode = result_data.get("mode")
    if mode == "wait_sub_agent_output":
        message = result_data.get("message") or ""
        header = f"已收到 {result_data.get('display_name') or '子智能体'} 的输出"
        if message:
            return f"{header}\n\n{message}"
        return header
    if mode == "wait_sub_agent_ids":
        results = result_data.get("results") or []
        header = result_data.get("message") or f"已等待 {len(results)} 个子智能体结束"
        detail_blocks: List[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            # 优先复用子智能体原始 system_message，确保与系统通知内容一致
            sys_msg = item.get("system_message")
            if isinstance(sys_msg, str) and sys_msg.strip():
                detail_blocks.append(sys_msg.strip())
            else:
                detail_blocks.append(_format_wait_sub_agent(item))
        if detail_blocks:
            return "\n\n".join([header] + detail_blocks)
        return header
    if mode == "wait_runcommand_id":
        nested = result_data.get("result")
        if isinstance(nested, dict):
            output = nested.get("output") or ""
            if output:
                return f"[后台 run_command 完成]\n{output}"
            return "[后台 run_command 完成]\n[no_output]"
        return "[后台 run_command 完成]"
    reason = result_data.get("reason")
    timestamp = result_data.get("timestamp")
    message = result_data.get("message") or "等待完成"
    parts = [message]
    if reason:
        parts.append(f"原因：{reason}")
    if timestamp:
        parts.append(f"时间：{timestamp}")
    return "；".join(parts)

def _format_run_command(result_data: Dict[str, Any]) -> str:
    status = str(result_data.get("status") or "").lower()
    if result_data.get("background_task_created") is False:
        body = _plain_command_output(result_data)
        if not body:
            body = "[no_output]"
        return "[命令在5秒内完成，未创建后台任务]\n" + body

    if status == "running_background":
        command_id = result_data.get("command_id") or "-"
        message = result_data.get("message") or "后台命令已创建；以下为当前已捕获输出。"
        output = result_data.get("output") or ""
        lines = [f"[{command_id}]", f"[{message}]"]
        if output:
            lines.append(output)
        else:
            lines.append("[no_output]")
        return "\n".join(lines)

    text = _plain_command_output(result_data)
    if status == "timeout":
        suggestion = "建议：在持久终端中直接运行该命令（terminal_session + terminal_input），或缩短命令执行时间。"
        text = f"{text}\n{suggestion}" if text else suggestion
    return text

def _format_terminal_snapshot(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("terminal_snapshot", result_data)
    session = result_data.get("session") or "default"
    requested = result_data.get("line_limit")
    requested_note = f"{requested} 行" if requested is not None else "全部"
    actual = result_data.get("lines_returned")
    actual_note = f"{actual} 行" if actual is not None else "未知行数"
    truncated = result_data.get("truncated")
    trunc_note = "已截断" if truncated else "未截断"
    output = result_data.get("output") or ""
    header = f"会话 {session}：请求 {requested_note}，实际返回 {actual_note}（{trunc_note}）。"
    body = _summarize_output_block(output, truncated)
    return f"{header}\n{body}"

def _format_command_result(label: str, result_data: Dict[str, Any]) -> str:
    command = result_data.get("command") or ""
    return_code = result_data.get("return_code")
    success = result_data.get("success")
    status = result_data.get("status")
    output = result_data.get("output")
    truncated = result_data.get("truncated")
    message = result_data.get("message")

    if success:
        header = f"{label}: `{command}`" if command else label
        if return_code is not None and return_code != "":
            header += f" (return_code={return_code})"
        lines = [header]
        if status and status not in {"completed", "success"}:
            lines.append(f"终端状态: {status}")
        if message:
            lines.append(message)
        lines.append(_summarize_output_block(output, truncated))
        return "\n".join(lines)

    error_msg = result_data.get("error") or message or "执行失败"
    header = f"⚠️ {label} 失败"
    if command:
        header += f"（命令 `{command}`）"
    lines = [f"{header}: {error_msg}"]
    if return_code not in {None, ""}:
        lines.append(f"返回码: {return_code}")
    if output:
        lines.append(_summarize_output_block(output, truncated))
    return "\n".join(lines)
