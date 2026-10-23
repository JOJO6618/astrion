"""Named public API over the persistent child execution engine."""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

_NAME_LOCK = threading.RLock()
PUBLIC_TOOLS = frozenset({
    "create_sub_agent", "send_message_to_sub_agent", "get_sub_agent_status",
    "stop_sub_agent", "terminate_sub_agent", "answer_sub_agent_question",
})


def validate_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
        raise ValueError("name必须为1至80个字符的非空名称。")
    value = value.strip()
    if any(ord(char) < 32 or char in '<>\\/"' for char in value):
        raise ValueError("name不能包含控制字符、路径分隔符或消息标签字符。")
    return value


def validate_timeout(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("timeout_seconds必须为正整数；不传表示本轮不限时。")
    return value


def public_agent(task: dict) -> dict:
    """Model-visible fields; internal identities belong to runtime views only."""
    return {key: task.get(key) for key in (
        "status", "summary", "created_at", "updated_at",
        "model_key", "intelligence", "access_level", "round_id", "end_reason", "final_result",
    )} | {
        "name": task["name"],
        "working_dir": task["working_dir"],
    }


def find_named(manager: Any, conversation_id: str, name: str) -> dict:
    name = validate_name(name)
    tasks = [task for task in manager.tasks.values()
             if task.get("conversation_id") == conversation_id
             and task["name"].casefold() == name.casefold()]
    if not tasks:
        raise ValueError(f"当前对话不存在子智能体「{name}」。")
    return max(tasks, key=lambda task: task.get("created_at") or 0)


def runtime_agent_view(task: dict) -> dict:
    """Preserve internal identities for frontend activity and runtime endpoints."""
    return {**public_agent(task), "task_id": task["task_id"],
            "conversation_id": task["conversation_id"]}


def query_agent_status(manager: Any, conversation_id: str, arguments: dict) -> dict:
    names = arguments.get("names")
    if "names" in arguments and (not isinstance(names, list) or not names):
        return {"success": False, "error": "names传入时必须为非空名称列表；省略可查询全部子智能体。"}
    manager.reconcile_task_states(conversation_id=conversation_id)
    if "names" not in arguments:
        rows = [{"found": True, **public_agent(task)}
                for task in list(manager.tasks.values())
                if task.get("conversation_id") == conversation_id]
    else:
        rows = []
        for name in names:
            try:
                rows.append({"found": True, **public_agent(find_named(manager, conversation_id, name))})
            except ValueError as exc:
                rows.append({"found": False, "name": name, "error": str(exc)})
    return {"success": True, "results": rows}


def create_named(manager: Any, terminal: Any, arguments: dict) -> dict:
    from modules.execution_scope import validate_child_access
    from modules.personalization_manager import load_personalization_config
    from modules.sub_agent.intelligence import resolve_intelligence_model
    from modules.sub_agent.prompts import build_system_prompt

    conversation_id = terminal.context_manager.current_conversation_id
    name = validate_name(arguments.get("name"))
    timeout = validate_timeout(arguments.get("timeout_seconds"))
    access = validate_child_access(terminal, arguments.get("access_level"))
    summary = str(arguments.get("summary") or "").strip()
    task = str(arguments.get("task") or "").strip()
    if not summary or not task:
        raise ValueError("summary和task不能为空。")
    prefs = load_personalization_config(str(terminal.data_dir)) or {}
    model = resolve_intelligence_model(arguments.get("intelligence"), prefs, terminal.api_client.model_key)
    with _NAME_LOCK:
        if any(item.get("conversation_id") == conversation_id
               and item["name"].casefold() == name.casefold()
               for item in manager.tasks.values()):
            raise ValueError(f"名称「{name}」已经使用；继续工作请发送消息，不要重复创建。")
        agent_id = manager.next_free_agent_id(conversation_id)
        working_dir = manager._resolve_working_dir(arguments.get("working_dir"), agent_id=agent_id)
        prompt = build_system_prompt(str(manager.project_path), "direct" if access == "full_access" else "sandbox")
        prompt += f"\n你的名称：{name}\n默认工作目录：{working_dir}\n"
        result = manager.create_sub_agent(
            agent_id=agent_id, summary=summary, task=task,
            conversation_id=conversation_id, access_level=access,
            model_key=model, intelligence=arguments.get("intelligence"),
            reasoning_effort=getattr(terminal.api_client, "reasoning_effort", None),
            thinking_mode="thinking", display_name=name,
            timeout_seconds=timeout, working_dir=str(working_dir),
            system_prompt=prompt,
            task_message=f"任务：{summary}\n\n{task}\n\n默认工作目录：{working_dir}",
            compress_threshold_tokens=int(prefs.get("sub_agent_compress_threshold_tokens") or 250000),
        )
        if not result.get("success"):
            return result
        record = manager.tasks[result["task_id"]]
        return {"success": True, **public_agent(record), "message": f"子智能体「{name}」已在后台开始工作，结束本轮后会自动通知。"}


async def handle_named_tool(terminal: Any, tool_name: str, arguments: dict) -> dict:
    manager = terminal.sub_agent_manager
    conversation_id = terminal.context_manager.current_conversation_id
    try:
        if tool_name == "create_sub_agent":
            return await asyncio.to_thread(create_named, manager, terminal, arguments)
        if tool_name == "answer_sub_agent_question":
            state = manager.get_multi_agent_state(conversation_id)
            ok = bool(state and state.provide_answer(str(arguments.get("question_id") or ""), str(arguments.get("answer") or "")))
            return {"success": ok, "question_id": arguments.get("question_id")}
        if tool_name == "get_sub_agent_status":
            return query_agent_status(manager, conversation_id, arguments)
        task = find_named(manager, conversation_id, arguments.get("name"))
        if task.get("status") == "terminated":
            raise ValueError("该子智能体已终结，不能继续激活。")
        if tool_name == "send_message_to_sub_agent":
            message = arguments.get("message")
            if not isinstance(message, str) or not message.strip():
                raise ValueError("message不能为空。")
            timeout = validate_timeout(arguments.get("timeout_seconds"))
            instance = manager._find_or_revive_sub_agent_task(task["agent_id"])
            if instance is None:
                raise ValueError("原上下文或固定权限不可用，无法恢复子智能体。")
            instance.enqueue_work(message.strip(), timeout=timeout)
            return {"success": True, "name": public_agent(task)["name"], "message": "消息已接收；本轮结束后会自动通知。"}
        if tool_name == "stop_sub_agent":
            result = manager.stop_sub_agent(task_id=task["task_id"], stopped_by="master")
        elif tool_name == "terminate_sub_agent":
            result = manager.terminate_sub_agent(task_id=task["task_id"])
        else:
            raise ValueError("不支持的子智能体工具。")
        result.pop("system_message", None)
        result["name"] = public_agent(task)["name"]
        return result
    except (ValueError, RuntimeError, OSError) as exc:
        return {"success": False, "error": str(exc)}
