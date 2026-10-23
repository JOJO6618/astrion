from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple
from utils.tool_result_formatter.common import (
    _format_failure, _preview_text, _summarize_output_block, _summarize_todo_tasks
)

from modules.i18n import tr

def _format_conversation_search(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("conversation_search", result_data)
    results = result_data.get("results") or []
    header = f"找到 {len(results)} 个当前工作区内的历史对话"
    filters: List[str] = []
    keywords = result_data.get("keywords")
    if isinstance(keywords, list):
        normalized_keywords = [str(item).strip() for item in keywords if str(item or "").strip()]
    else:
        normalized_keywords = []
    if normalized_keywords:
        filters.append(f"关键词：{' / '.join(normalized_keywords)}")
    elif result_data.get("query"):
        filters.append(f"关键词：{result_data.get('query')}")
    if result_data.get("start_date") or result_data.get("end_date"):
        filters.append(f"日期：{result_data.get('start_date') or '不限'} ~ {result_data.get('end_date') or '不限'}")
    if result_data.get("excluded_conversation_id"):
        filters.append("已排除当前对话")
    if filters:
        header += "（" + "；".join(filters) + "）"
    if not results:
        return header + "\n未找到匹配对话。"
    lines = [header]
    for idx, item in enumerate(results, start=1):
        lines.append(f"{idx}. {item.get('id')}")
        lines.append(f"   标题：{item.get('title') or '未命名对话'}")
        if item.get("total_messages") is not None or item.get("total_tools") is not None:
            lines.append(
                f"   规模：{int(item.get('total_messages') or 0)} 条消息，{int(item.get('total_tools') or 0)} 个工具"
            )
        if item.get("first_user_message"):
            lines.append(f"   首条用户消息：{item.get('first_user_message')}")
        if item.get("created_at"):
            lines.append(f"   创建时间：{item.get('created_at')}")
        if item.get("updated_at"):
            lines.append(f"   更新时间：{item.get('updated_at')}")
    return "\n".join(lines)

def _format_conversation_review(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("conversation_review", result_data)
    content_mode = result_data.get("content_mode")
    mode_note = ""
    if content_mode == "full":
        mode_note = "（完整记录，含工具调用与结果）"
    elif content_mode == "dialogue":
        mode_note = "（纯净对话，仅含实际对话内容）"
    if result_data.get("mode") == "read" and result_data.get("content"):
        lines = [f"对话回顾内容{mode_note}："]
        if result_data.get("title"):
            lines.append(f"标题：{result_data.get('title')}")
        if result_data.get("char_count") is not None:
            lines.append(f"字符数：{result_data.get('char_count')}")
        lines.append("")
        lines.append(str(result_data.get("content") or ""))
        return "\n".join(lines)
    path = result_data.get("path") or ""
    if result_data.get("too_long"):
        lines = [
            f"对话回顾内容太长（{result_data.get('char_count')} 字符），已保存到文件：",
            str(path),
            "请使用 read_file 分段或查找阅读该文件。",
        ]
        if result_data.get("title"):
            lines.insert(1, f"标题：{result_data.get('title')}")
        return "\n".join(lines)
    lines = [
        f"已生成对话回顾文件{mode_note}：",
        str(path),
    ]
    if result_data.get("title"):
        lines.append(f"标题：{result_data.get('title')}")
    if result_data.get("char_count") is not None:
        lines.append(f"字符数：{result_data.get('char_count')}")
    lines.append("请使用 read_file 读取该文件。")
    return "\n".join(lines)

def _format_todo_create(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("todo_create", result_data)
    todo = (result_data.get("todo_list") or {}).copy()
    overview = todo.get("overview") or "未命名任务"
    total = len(todo.get("tasks") or [])
    return f"已创建 TODO：{overview}（共 {total} 项）"

def _format_todo_update_task(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("todo_update_task", result_data)
    message = result_data.get("message") or tr("fmt_agent.todo_update_default")
    todo = result_data.get("todo_list") or {}
    tasks = todo.get("tasks") or []
    total = len(tasks)
    done = sum(1 for t in tasks if t.get("status") == "done")
    progress_note = f"进度 {done}/{total}" if total else ""
    return f"{message}；{progress_note}".strip("；")

def _format_update_memory(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("update_memory", result_data)
    operation = result_data.get("operation") or "write"
    idx = result_data.get("index")
    count = result_data.get("count")
    if operation == "append":
        suffix = f"（共 {count} 条）" if count is not None else ""
        return f"记忆已追加新条目{suffix}"
    if operation == "replace":
        return f"记忆第 {idx} 条已替换。"
    if operation == "delete":
        suffix = f"（剩余 {count} 条）" if count is not None else ""
        return f"记忆第 {idx} 条已删除{suffix}"
    return f"记忆已更新。"

def _format_sub_agent_stats(stats: Optional[Dict[str, Any]]) -> str:
    if not isinstance(stats, dict):
        return ""

    def _to_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    api_calls = _to_int(stats.get("api_calls") or stats.get("api_call_count") or stats.get("turn_count"))
    files_read = _to_int(stats.get("files_read"))
    edit_files = _to_int(stats.get("edit_files"))
    searches = _to_int(stats.get("searches"))
    web_pages = _to_int(stats.get("web_pages"))
    commands = _to_int(stats.get("commands"))
    lines = [
        tr("fmt_agent2.api_calls", n=api_calls),
        tr("fmt_agent2.files_read", n=files_read),
        tr("fmt_agent2.edit_files", n=edit_files),
        tr("fmt_agent2.searches", n=searches),
        tr("fmt_agent2.web_pages", n=web_pages),
        tr("fmt_agent2.commands", n=commands),
    ]
    return "\n".join(lines)

def _format_create_sub_agent(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("create_sub_agent", result_data)
    lines = [tr("fmt_agent2.created_with_status", name=result_data["name"],
                status=result_data.get("status", "running"))]
    lines.append(f"固定权限：{result_data.get('access_level', '')}")
    lines.append(f"工作目录：{result_data.get('working_dir', '')}")
    return "\n".join(lines)


def _format_get_sub_agent_status(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("get_sub_agent_status", result_data)
    blocks = []
    for item in result_data.get("results", []):
        name = item["name"]
        if not item.get("found"):
            blocks.append(tr("fmt_agent2.label_not_found", label=name))
            continue
        lines = [tr("fmt_agent2.label_status", label=name, status=item.get("status"))]
        if item.get("end_reason"):
            lines.append(f"本轮结束原因：{item['end_reason']}")
        stats = _format_sub_agent_stats(item.get("stats"))
        if stats:
            lines.append(stats)
        if item.get("final_result"):
            lines.append(item["final_result"])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or tr("fmt_agent2.no_status_results")


def _format_load_tools(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("load_tools", result_data)
    tools = result_data.get("tools") or []
    names = [
        name for name in ((t.get("function") or {}).get("name") for t in tools)
        if name
    ]
    header = result_data.get("message") or tr(
        "fmt_tool_loading.loaded", n=len(tools), names=", ".join(names)
    )
    parts = [header]
    already = result_data.get("already_loaded") or []
    if already:
        parts.append(tr("fmt_tool_loading.already", names=", ".join(already)))
    # 关键：完整 JSON 定义必须保留在上下文输出里——模型后续调用的依据就是
    # 这份 schema；若只写摘要，历史重建后模型将失去参数结构（与压缩重置
    # 同源设计：定义只存在于 tool result 中）。
    parts.append(tr("fmt_tool_loading.defs_header"))
    parts.append("```json\n" + json.dumps(tools, ensure_ascii=False, indent=2) + "\n```")
    return "\n\n".join(parts)


def _format_terminate_sub_agent(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("terminate_sub_agent", result_data)
    return tr("fmt_agent2.force_closed_display", name=result_data["name"])


def _format_send_message_to_sub_agent(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("send_message_to_sub_agent", result_data)
    return tr("fmt_agent2.sent_to_display", name=result_data["name"])


def _format_stop_sub_agent(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("stop_sub_agent", result_data)
    return f"{result_data['name']}：{result_data.get('message') or tr('fmt_agent2.paused')}"


def _format_answer_sub_agent_question(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("answer_sub_agent_question", result_data)
    question_id = result_data.get("question_id")
    if question_id:
        return tr("fmt_agent2.answered_question", question_id=question_id)
    return tr("fmt_agent2.answered_plain")


def _format_submit_plan(result_data: Dict[str, Any]) -> str:
    """submit_plan 工具结果 → 对话上下文摘要（计划批准流的两态）。"""
    status = str(result_data.get("status") or "").strip()
    plan_file = str(result_data.get("plan_file") or "").strip()
    comment = str(result_data.get("comment") or "").strip()
    message = str(result_data.get("message") or "").strip()

    if status == "approved":
        parts = [f"✅ 用户已批准计划（{plan_file}），运行模式已切换为「执行」。"]
        if comment:
            parts.append(f"用户批准意见：{comment}")
        parts.append("现在可以开始按计划实施。")
        return "\n".join(parts)

    if status == "rejected":
        parts = [f"❌ 用户拒绝了计划（{plan_file}），仍处于计划模式。"]
        if comment:
            parts.append(f"用户意见：{comment}")
        else:
            parts.append("用户未填写具体意见。")
        parts.append("请修订计划文档后重新提交。")
        return "\n".join(parts)

    # 校验失败 / 未在计划模式 / 超时等其他状态
    if message:
        return f"⚠️ submit_plan 未完成（{status or 'unknown'}）：{message}"
    return _format_failure("submit_plan", result_data)
