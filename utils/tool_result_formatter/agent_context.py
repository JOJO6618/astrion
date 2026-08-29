from __future__ import annotations

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
    if result_data.get("mode") == "read" and result_data.get("content"):
        lines = ["对话回顾内容："]
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
        "已生成对话回顾文件：",
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
    # 多智能体模式：对模型只暴露角色内编号显示名，全局 agent_id/task_id 为内部细节
    display_name = result_data.get("display_name")
    status = result_data.get("status")
    if display_name:
        return tr("fmt_agent2.created_with_status", name=display_name, status=status or "running")
    agent_id = result_data.get("agent_id")
    task_id = result_data.get("task_id")
    refs = result_data.get("copied_references") or []
    ref_note = tr("fmt_agent2.ref_count_note", n=len(refs)) if refs else ""
    deliver_dir = result_data.get("deliverables_dir")
    deliver_note = tr("fmt_agent2.deliver_dir_note", dir=deliver_dir) if deliver_dir else ""
    header = tr(
        "fmt_agent2.created_header",
        agent_id=agent_id,
        task_id=task_id,
        status=status,
        ref_note=ref_note,
        deliver_note=deliver_note,
    )
    stats_text = _format_sub_agent_stats(
        result_data.get("stats") or (result_data.get("final_result") or {}).get("stats")
    )
    summary = result_data.get("message") or result_data.get("summary")
    elapsed_seconds = result_data.get("runtime_seconds")
    if elapsed_seconds is None:
        elapsed_seconds = result_data.get("elapsed_seconds")
    lines = [header]
    if stats_text:
        lines.append(stats_text)
    if status == "completed" and isinstance(elapsed_seconds, (int, float)):
        lines.append(tr("fmt_agent2.running_seconds", n=int(round(elapsed_seconds))))
    if summary and status in {"completed", "failed", "timeout", "terminated"}:
        lines.append(str(summary))
    return "\n".join(lines)

def _format_wait_sub_agent(result_data: Dict[str, Any]) -> str:
    task_id = result_data.get("task_id")
    agent_id = result_data.get("agent_id")
    status = result_data.get("status")
    stats_value = result_data.get("stats")
    if not isinstance(stats_value, dict) and status == "timeout":
        stats_value = {}
    stats_text = _format_sub_agent_stats(stats_value)
    elapsed_seconds = result_data.get("runtime_seconds")
    if elapsed_seconds is None:
        elapsed_seconds = result_data.get("elapsed_seconds")
    if result_data.get("success"):
        copied_path = result_data.get("copied_path") or result_data.get("deliverables_path")
        message = result_data.get("message") or tr("fmt_agent2.task_completed")
        deliver_note = (
            tr("fmt_agent2.copied_to", path=copied_path)
            if copied_path
            else tr("fmt_agent2.deliver_generated")
        )
        lines = [tr("fmt_agent2.completed_header", agent_id=agent_id, task_id=task_id)]
        if stats_text:
            lines.append(stats_text)
        if isinstance(elapsed_seconds, (int, float)):
            lines.append(tr("fmt_agent2.running_seconds", n=int(round(elapsed_seconds))))
        lines.append(message)
        lines.append(deliver_note)
        return "\n".join(lines)
    message = result_data.get("message") or result_data.get("error") or tr("fmt_agent2.task_failed")
    lines = [tr("fmt_agent2.abnormal_header", agent_id=agent_id, task_id=task_id, status=status)]
    if stats_text:
        lines.append(stats_text)
    lines.append(message)
    return "\n".join(lines)

def _format_get_sub_agent_status(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("get_sub_agent_status", result_data)
    results = result_data.get("results") or []
    if not results:
        return tr("fmt_agent2.no_status_results")
    blocks = []
    for item in results:
        # 多智能体模式：优先使用角色内编号显示名，不暴露全局 agent_id
        agent_id = item.get("agent_id")
        label = item.get("display_name") or f"#{agent_id}"
        if not item.get("found"):
            blocks.append(tr("fmt_agent2.label_not_found", label=label))
            continue
        status = item.get("status")
        summary = None
        final_result = item.get("final_result") or {}
        elapsed_seconds = None
        if isinstance(final_result, dict):
            summary = final_result.get("message") or final_result.get("summary")
            elapsed_seconds = final_result.get("runtime_seconds")
            if elapsed_seconds is None:
                elapsed_seconds = final_result.get("elapsed_seconds")
        if not summary:
            summary = item.get("summary") or ""
        stats_text = _format_sub_agent_stats(item.get("stats"))

        if status == "completed":
            lines = [tr("fmt_agent2.label_completed", label=label)]
        elif status == "terminated":
            lines = [tr("fmt_agent2.label_terminated", label=label)]
        elif status in {"failed", "timeout"}:
            lines = [tr("fmt_agent2.label_abnormal", label=label, status=status)]
        else:
            lines = [tr("fmt_agent2.label_status", label=label, status=status)]
        if stats_text:
            lines.append(stats_text)
        if status == "completed" and isinstance(elapsed_seconds, (int, float)):
            lines.append(tr("fmt_agent2.running_seconds", n=int(round(elapsed_seconds))))
        if summary:
            lines.append(str(summary))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

def _format_close_sub_agent(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("close_sub_agent", result_data)
    message = result_data.get("message") or tr("fmt_agent2.closed")
    task_id = result_data.get("task_id")
    status = result_data.get("status")
    status_note = tr("fmt_agent2.status_note", status=status) if status else ""
    return f"{message}{status_note}（task_id={task_id}）"


def _format_terminate_sub_agent(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("terminate_sub_agent", result_data)
    display_name = result_data.get("display_name")
    if display_name:
        return tr("fmt_agent2.force_closed_display", name=display_name)
    agent_id = result_data.get("agent_id")
    task_id = result_data.get("task_id")
    message = result_data.get("message") or tr("fmt_agent2.force_closed")
    if agent_id is not None:
        return tr("fmt_agent2.force_closed_id", agent_id=agent_id, task_id=task_id)
    return f"{message}（task_id={task_id}）"


def _format_send_message_to_sub_agent(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("send_message_to_sub_agent", result_data)
    display_name = result_data.get("display_name")
    if display_name:
        return tr("fmt_agent2.sent_to_display", name=display_name)
    agent_id = result_data.get("agent_id")
    if agent_id is not None:
        return tr("fmt_agent2.sent_to_id", agent_id=agent_id)
    return tr("fmt_agent2.sent_plain")


def _format_stop_sub_agent(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("stop_sub_agent", result_data)
    display_name = result_data.get("display_name")
    message = result_data.get("message") or tr("fmt_agent2.paused")
    if display_name:
        # manager 返回的 message 已是「{显示名} 已暂停…」格式，直接返回避免重复
        return message
    agent_id = result_data.get("agent_id")
    if agent_id is not None:
        return tr("fmt_agent2.paused_id", agent_id=agent_id, message=message)
    return message


def _format_answer_sub_agent_question(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("answer_sub_agent_question", result_data)
    question_id = result_data.get("question_id")
    if question_id:
        return tr("fmt_agent2.answered_question", question_id=question_id)
    return tr("fmt_agent2.answered_plain")


def _format_create_custom_agent(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("create_custom_agent", result_data)
    role_id = result_data.get("role_id")
    name = result_data.get("name") or role_id
    if result_data.get("overwritten"):
        return f"已覆盖更新角色 {role_id}（{name}）。新设定对之后创建的实例生效，运行中的实例不受影响。"
    return f"已创建自定义角色 {role_id}（{name}）。"


def _format_list_agents(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("list_agents", result_data)
    roles = result_data.get("roles") or []
    if not roles:
        return "当前没有可用角色。"
    lines = [f"可用角色（共 {len(roles)} 个）：", ""]
    for idx, role in enumerate(roles, start=1):
        role_id = role.get("role_id") or "未知"
        name = role.get("name") or role_id
        description = role.get("description") or ""
        thinking_mode = role.get("thinking_mode") or "fast"
        is_custom = "是" if role.get("is_custom") else "否"
        lines.append(f"{idx}. {role_id} — {name}")
        if description:
            lines.append(f"   描述：{description}")
        lines.append(f"   思考模式：{thinking_mode} | 自定义：{is_custom}")
    return "\n".join(lines)


def _format_list_active_sub_agents(result_data: Dict[str, Any]) -> str:
    if not result_data.get("success"):
        return _format_failure("list_active_sub_agents", result_data)
    return _format_active_sub_agents_list(result_data.get("agents") or [])


def _format_active_sub_agents_list(agents: List[Dict[str, Any]]) -> str:
    if not agents:
        return "当前会话没有活跃子智能体。"
    lines = [f"当前会话活跃子智能体（共 {len(agents)} 个）：", ""]
    for agent in agents:
        agent_id = agent.get("agent_id") or "?"
        display_name = agent.get("display_name") or f"Agent_{agent_id}"
        status = agent.get("status") or "unknown"
        summary = agent.get("summary") or ""
        last_output = agent.get("last_output") or ""
        # 只暴露角色内编号显示名，不暴露全局 agent_id
        lines.append(f"{display_name} [{status}]")
        if summary:
            lines.append(f"   任务：{summary}")
        if last_output:
            preview = last_output[:120]
            suffix = "…" if len(last_output) > 120 else ""
            lines.append(f"   最近输出：{preview}{suffix}")
    return "\n".join(lines)


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
