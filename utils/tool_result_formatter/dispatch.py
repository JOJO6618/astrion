"""将工具执行结果转换为对话上下文可用的纯文本摘要。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from utils.tool_result_formatter.mcp import (
    _looks_like_mcp_payload,
    extract_mcp_content_for_context,
)
from utils.tool_result_formatter.file import (
    format_read_file_result,
    _format_write_file_diff,
    _format_create_file,
    _format_write_file,
    _format_edit_file,
    _format_delete_file,
    _format_rename_file,
    _format_create_folder,
)
from utils.tool_result_formatter.terminal import (
    _format_terminal_snapshot,
    _format_terminal_session,
    _format_terminal_input,
    _format_sleep,
    _format_run_command,
    _format_command_result,
)
from utils.tool_result_formatter.agent_context import (
    _format_conversation_search,
    _format_conversation_review,
    _format_todo_create,
    _format_todo_update_task,
    _format_update_memory,
    _format_create_sub_agent,
    _format_close_sub_agent,
    _format_get_sub_agent_status,
    _format_terminate_sub_agent,
    _format_send_message_to_sub_agent,
    _format_stop_sub_agent,
    _format_answer_sub_agent_question,
    _format_create_custom_agent,
    _format_list_agents,
    _format_list_active_sub_agents,
    _format_submit_plan,
)
from utils.tool_result_formatter.web_media import (
    _format_extract_webpage,
    _format_vlm_analyze,
    _format_ocr_image,
    _format_trigger_easter_egg,
    _format_create_skill,
    _format_manage_personalization,
    _format_list_workflows,
    _format_save_workflow,
)
from utils.tool_result_formatter.common import _format_failure

def format_tool_result_for_context(function_name: str, result_data: Any, raw_text: str = "") -> str:
    """根据工具名称输出纯文本摘要，必要时附加关键信息。"""
    # 自定义工具统一格式
    if isinstance(result_data, dict) and result_data.get("custom_tool"):
        tool_id = result_data.get("tool_id") or function_name
        success = result_data.get("success")
        status = "成功" if success else "失败"
        message = result_data.get("message") or result_data.get("summary") or ""
        output = result_data.get("output") or ""
        parts = [f"[自定义工具] {tool_id} 执行{status}"]
        if message:
            parts.append(str(message))
        if output:
            parts.append(str(output))
        return "\n".join(parts).strip() or raw_text

    if _looks_like_mcp_payload(function_name, result_data):
        parsed = extract_mcp_content_for_context(result_data if isinstance(result_data, dict) else {})
        text = str(parsed.get("text") or "").strip()
        if text:
            return text
        return ""

    if function_name in {"read_file", "read_skill", "recall_project_memory"} and isinstance(result_data, dict):
        return format_read_file_result(result_data)

    if function_name == "search_project_memory" and isinstance(result_data, dict):
        content_text = str(result_data.get("content") or "").strip()
        if content_text:
            return content_text
        return str(result_data.get("summary") or result_data.get("error") or "")

    if function_name == "write_file_diff" and isinstance(result_data, dict):
        return _format_write_file_diff(result_data, raw_text)

    if function_name == "conversation_search" and isinstance(result_data, dict):
        return _format_conversation_search(result_data)

    if function_name == "conversation_review" and isinstance(result_data, dict):
        return _format_conversation_review(result_data)

    if not isinstance(result_data, dict):
        return raw_text

    handler = TOOL_FORMATTERS.get(function_name)
    if handler:
        return handler(result_data)

    summary = result_data.get("summary") or result_data.get("message")
    error_msg = result_data.get("error")
    parts: List[str] = []
    if summary:
        parts.append(str(summary))
    if error_msg:
        parts.append(f"⚠️ 错误: {error_msg}")
    return "\n".join(parts) if parts else raw_text


TOOL_FORMATTERS = {
    "create_file": _format_create_file,
    "write_file": _format_write_file,
    "edit_file": _format_edit_file,
    "delete_file": _format_delete_file,
    "rename_file": _format_rename_file,
    "create_folder": _format_create_folder,
    "terminal_snapshot": _format_terminal_snapshot,
    "terminal_session": _format_terminal_session,
    "terminal_input": _format_terminal_input,
    "sleep": _format_sleep,
    "run_command": _format_run_command,
    "extract_webpage": _format_extract_webpage,
    "vlm_analyze": _format_vlm_analyze,
    "ocr_image": _format_ocr_image,
    "trigger_easter_egg": _format_trigger_easter_egg,
    "todo_create": _format_todo_create,
    "todo_update_task": _format_todo_update_task,
    "update_memory": _format_update_memory,
    "create_skill": _format_create_skill,
    "list_workflows": _format_list_workflows,
    "save_workflow": _format_save_workflow,
    "manage_personalization": _format_manage_personalization,
    "create_sub_agent": _format_create_sub_agent,
    "close_sub_agent": _format_close_sub_agent,
    "terminate_sub_agent": _format_terminate_sub_agent,
    "send_message_to_sub_agent": _format_send_message_to_sub_agent,
    "stop_sub_agent": _format_stop_sub_agent,
    "answer_sub_agent_question": _format_answer_sub_agent_question,
    "create_custom_agent": _format_create_custom_agent,
    "list_agents": _format_list_agents,
    "list_active_sub_agents": _format_list_active_sub_agents,
    "get_sub_agent_status": _format_get_sub_agent_status,
    "submit_plan": _format_submit_plan,
}
