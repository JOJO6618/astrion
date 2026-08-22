"""子智能体工具定义、结果格式化与模型配置解析。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.model_profiles import _parse_env_ref

# 子智能体可用工具定义（与前端进度展示兼容）
SUB_AGENT_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取/搜索/抽取 UTF-8 文本文件。type=read/search/extract，仅单文件操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径：工作区内请用相对路径，工作区外可用绝对路径。"},
                    "type": {"type": "string", "enum": ["read", "search", "extract"], "description": "读取模式。"},
                    "max_chars": {"type": "integer", "description": "返回内容最大字符数，超出将截断。"},
                    "start_line": {"type": "integer", "description": "[read] 起始行号（1-based）。"},
                    "end_line": {"type": "integer", "description": "[read] 结束行号（>= start_line）。"},
                    "query": {"type": "string", "description": "[search] 搜索关键词。"},
                    "max_matches": {"type": "integer", "description": "[search] 最多返回多少条命中窗口。"},
                    "context_before": {"type": "integer", "description": "[search] 命中行向上追加行数。"},
                    "context_after": {"type": "integer", "description": "[search] 命中行向下追加行数。"},
                    "case_sensitive": {"type": "boolean", "description": "[search] 是否区分大小写。"},
                    "segments": {
                        "type": "array",
                        "description": "[extract] 需要抽取的行区间数组。",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                            },
                            "required": ["start_line", "end_line"],
                        },
                        "minItems": 1,
                    },
                },
                "required": ["path", "type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖文件；append=true 时追加内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径：工作区内请用相对路径。"},
                    "content": {"type": "string", "description": "要写入的内容。"},
                    "append": {"type": "boolean", "default": False, "description": "是否追加到文件末尾。"},
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "在文件中执行一处或多处精确字符串替换；建议先用 read_file 精确复制 old_string。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径：工作区内请用相对路径。"},
                    "replacements": {
                        "type": "array",
                        "description": "替换组列表，每组包含 old_string/new_string/replace_all。",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_string": {"type": "string"},
                                "new_string": {"type": "string"},
                                "replace_all": {"type": "boolean", "default": False, "description": "是否替换所有匹配内容。"},
                            },
                            "required": ["old_string", "new_string"],
                        },
                        "minItems": 1,
                    },
                },
                "required": ["file_path", "replacements"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在当前终端环境执行一次性命令（非交互式）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令。"},
                    "timeout": {"type": "number", "description": "超时时间（秒），必填。"},
                    "working_dir": {"type": "string", "description": "工作目录：工作区内请用相对路径。"},
                },
                "required": ["command", "timeout"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "网络搜索（Tavily）。用于外部资料或最新信息检索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "topic": {"type": "string", "enum": ["general", "news", "finance"]},
                    "time_range": {"type": "string", "enum": ["day", "week", "month", "year", "d", "w", "m", "y"]},
                    "days": {"type": "integer"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "country": {"type": "string"},
                    "include_domains": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_webpage",
            "description": "网页内容提取（Tavily）。mode=read 直接返回内容，mode=save 保存为文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["read", "save"]},
                    "url": {"type": "string"},
                    "target_path": {"type": "string", "description": "[save] 保存路径，必须是 .md 文件。"},
                },
                "required": ["mode", "url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_mediafile",
            "description": "读取图片或视频文件，以 base64 形式返回给模型。非媒体文件将拒绝。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_skill",
            "description": "按 skill 名称读取 .astrion/skills/<name>/SKILL.md 内容；内部等价于 read_file 的 read 模式，并返回解析后的 path。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "skill 名称（支持 skill id 或技能名称）"},
                },
                "required": ["skill_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_project_memory",
            "description": "读取指定的项目记忆文件，返回完整内容（含 frontmatter）。项目记忆存储在 .astrion/memory/ 目录下。通常在 search_project_memory 检索定位后使用；若不确定记忆名称，先检索再读取。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "记忆名称，对应 .astrion/memory/{name}.md 文件名"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_project_memory",
            "description": "在项目记忆文件（.astrion/memory/*.md）的正文和描述中全文检索，返回匹配的记忆文件与片段。【硬性要求】开始处理与本项目相关的任务前（修改代码、调试报错、配置部署，或任务可能涉及项目约定/历史决策/已知问题时），必须先调用本工具检索；根据任务内容和项目记忆索引中的描述，提取2~5个清晰精确的关键词（优先使用项目术语、模块名、文件名、报错文本，中英文均可）。与本项目无关的通用问题不要调用。未命中时会明确返回无结果，此时不要更换同义词重复尝试；命中后如需完整内容，用 recall_project_memory 读取。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "description": "搜索关键词列表，建议2~5个；任一关键词命中即算匹配，命中数越多排名越靠前",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 5,
                    },
                    "max_results": {"type": "integer", "description": "最多返回多少条匹配记忆，默认5，最大10"},
                },
                "required": ["keywords"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_create",
            "description": "创建待办列表，最多 8 条任务；若已有列表将被覆盖。当任务复杂、预计需要超过 3 个步骤时，应积极创建待办事项。",
            "parameters": {
                "type": "object",
                "properties": {
                    "overview": {"type": "string", "description": "一句话概述待办清单要完成的目标，50 字以内。"},
                    "tasks": {
                        "type": "array",
                        "description": "任务列表，1~8 条，每条写清“动词+对象+目标”。",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "单个任务描述，写成可执行的步骤"}
                            },
                            "required": ["title"]
                        },
                        "minItems": 1,
                        "maxItems": 8
                    },
                },
                "required": ["overview", "tasks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_update_task",
            "description": "批量勾选或取消任务（支持单个或多个任务）；全部勾选时提示所有任务已完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_index": {"type": "integer", "description": "任务序号（1-8），兼容旧参数"},
                    "task_indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 1,
                        "maxItems": 8,
                        "description": "要更新的任务序号列表（1-8），可一次勾选多个"
                    },
                    "completed": {"type": "boolean", "description": "true=打勾，false=取消"},
                },
                "required": ["completed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_webpage",
            "description": "提取网页内容并保存为纯文本文件，适合需要长期留存的长文档。请提供网址与目标路径（含 .txt 后缀），落地后请通过终端命令查看。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要保存的网页URL"},
                    "target_path": {"type": "string", "description": "保存位置，包含文件名，相对于项目根目录"},
                },
                "required": ["url", "target_path"],
            },
        },
    },
]

FINISH_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "finish_task",
        "description": "完成当前任务并退出。调用此工具表示你已经完成了分配的任务，所有交付文件已准备好。",
        "parameters": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "summary": {"type": "string", "description": "任务完成摘要（50-200字）。"},
            },
            "required": ["success", "summary"],
        },
    },
}


def _format_tool_result(name: str, raw: Any) -> str:
    """把主进程返回的工具结果格式化为文本，供 LLM 消费。"""
    if not isinstance(raw, dict):
        return str(raw) if raw is not None else ""
    if raw.get("success") is False:
        return raw.get("error") or raw.get("message") or "失败"
    if name == "read_file":
        if raw.get("type") == "read":
            return raw.get("content") or ""
        if raw.get("type") == "search":
            parts = []
            for match in raw.get("matches") or []:
                hits = ",".join(str(h) for h in (match.get("hits") or []))
                parts.append(f"[{match.get('id')}] L{match.get('line_start')}-{match.get('line_end')} hits:{hits}")
                parts.append(match.get("snippet") or "")
                parts.append("")
            return "\n".join(parts).strip()
        if raw.get("type") == "extract":
            parts = []
            for seg in raw.get("segments") or []:
                label = seg.get("label") or "segment"
                parts.append(f"[{label}] L{seg.get('line_start')}-{seg.get('line_end')}")
                parts.append(seg.get("content") or "")
                parts.append("")
            return "\n".join(parts).strip()
        return ""
    if name == "write_file":
        return raw.get("message") or "写入成功"
    if name == "edit_file":
        count = raw.get("replacements")
        msg = raw.get("message") or ""
        return f"已替换 {count} 处: {raw.get('path')}{'，' + msg if msg else ''}"
    if name == "run_command":
        return raw.get("output") or ""
    if name == "web_search":
        lines = [f"🔍 搜索查询: {raw.get('query')}", f"📅 搜索时间: {raw.get('searched_at') or datetime.now().isoformat()}", "", "📝 AI摘要:", raw.get("summary") or "", "", "---", "", "📊 搜索结果:"]
        for idx, item in enumerate(raw.get("results") or [], 1):
            lines.append(f"{idx}. {item.get('title') or ''}")
            lines.append(f"   🔗 {item.get('url') or ''}")
            lines.append(f"   📄 {item.get('content') or ''}")
        return "\n".join(lines).strip()
    if name == "extract_webpage":
        if raw.get("mode") == "save":
            return f"已保存: {raw.get('path')}"
        return f"URL: {raw.get('url')}\n{raw.get('content') or ''}"
    if name == "read_mediafile":
        return raw.get("message") or "媒体已读取"
    if name == "read_skill":
        # read_skill 结果结构与 read_file(read) 类似
        if raw.get("content"):
            return raw.get("content")
        return raw.get("message") or "skill 已读取"
    if name == "recall_project_memory":
        # recall_project_memory 返回记忆文件完整内容
        if raw.get("content"):
            return raw.get("content")
        return raw.get("message") or raw.get("error") or "记忆已读取"
    if name == "search_project_memory":
        # search_project_memory 返回预格式化的检索结果文本
        if raw.get("content"):
            return raw.get("content")
        return raw.get("message") or raw.get("error") or "检索完成"
    if name == "todo_create":
        return raw.get("message") or "待办列表已创建"
    if name == "todo_update_task":
        return raw.get("message") or "任务状态已更新"
    if name == "save_webpage":
        if raw.get("path"):
            return f"已保存: {raw.get('path')}"
        return raw.get("message") or "网页已保存"
    # 多智能体通信工具
    if name == "ask_master":
        if raw.get("success"):
            return f"Team Leader 的回答：\n{raw.get('answer') or ''}"
        return raw.get("error") or "向 Team Leader 提问失败"
    if name == "ask_other_agent":
        if raw.get("success"):
            return f"子智能体的回答：\n{raw.get('answer') or ''}"
        return raw.get("error") or "向其他子智能体提问失败"
    if name == "answer_other_agent":
        if raw.get("success"):
            return "回复已发送。"
        return raw.get("error") or "回复失败"
    if name == "list_active_sub_agents":
        from utils.tool_result_formatter.agent_context import _format_active_sub_agents_list
        return _format_active_sub_agents_list(raw.get("agents") or [])
    return json.dumps(raw, ensure_ascii=False)


def _build_sub_agent_profile(model_raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把 sub_agent_models.json 中的模型条目转成 APIClient.apply_profile 所需格式。"""
    name = str(model_raw.get("name") or model_raw.get("model_name") or model_raw.get("model") or "").strip()
    url = str(_parse_env_ref(model_raw.get("url") or model_raw.get("base_url") or "") or "").strip()
    api_key = str(_parse_env_ref(model_raw.get("apikey") or model_raw.get("api_key") or "") or "").strip()
    if not name or not url or not api_key:
        return None

    modes_text = str(model_raw.get("modes") or model_raw.get("mode") or model_raw.get("supported_modes") or "").lower()
    supports_thinking = "thinking" in modes_text or "思考" in modes_text
    fast_only = modes_text == "fast" or modes_text == "快速"

    multimodal_text = str(model_raw.get("multimodal") or model_raw.get("multi_modal") or model_raw.get("multi") or "none").lower()
    multimodal = "none"
    if "video" in multimodal_text:
        multimodal = "image+video"
    elif "image" in multimodal_text:
        multimodal = "image"

    max_output = _to_int(model_raw.get("max_output") or model_raw.get("max_tokens") or model_raw.get("max_output_tokens"))
    max_context = _to_int(model_raw.get("max_context") or model_raw.get("context_window") or model_raw.get("max_context_tokens"))

    model_id = str(model_raw.get("model_id") or name).strip()
    extra = _pick_dict(model_raw, ["extra_parameter", "extra_params", "extra"])
    # 优先从顶层查找 fast/thinking extra_parameter；如果没有，从 thinkmode_status 中查找
    fast_extra = _pick_dict(model_raw, ["fast_extra_parameter", "fast_extra_params", "fast_extra"])
    thinking_extra = _pick_dict(model_raw, ["thinking_extra_parameter", "thinking_extra_params", "thinking_extra"])
    # 从 thinkmode_status 中提取（与主智能体 custom_models.json 结构对齐）
    thinkmode_status = model_raw.get("thinkmode_status") or {}
    if isinstance(thinkmode_status, dict):
        if not fast_extra:
            fast_extra = _pick_dict(thinkmode_status, ["fast_extra_parameter", "fast_extra_params", "fast_extra"])
        if not thinking_extra:
            thinking_extra = _pick_dict(thinkmode_status, ["thinking_extra_parameter", "thinking_extra_params", "thinking_extra"])
        # thinkmode_status 内部的 model_id 可以覆盖默认 model_id
        ts_model_id = str(thinkmode_status.get("model_id") or "").strip()
        if ts_model_id:
            model_id = ts_model_id

    profile: Dict[str, Any] = {
        "name": name,
        "multimodal": multimodal,
        "context_window": max_context,
        "supports_thinking": supports_thinking,
        "fast_only": fast_only,
        "fast": {
            "base_url": url.rstrip("/"),
            "api_key": api_key,
            "model_id": model_id,
            "max_tokens": max_output,
            "context_window": max_context,
            "extra_params": {**extra, **fast_extra},
        },
    }
    if supports_thinking:
        profile["thinking"] = {
            "base_url": url.rstrip("/"),
            "api_key": api_key,
            "model_id": model_id,
            "max_tokens": max_output,
            "context_window": max_context,
            "extra_params": {**extra, **thinking_extra},
        }
    else:
        # 即使不支持 thinking，也保留 fast 的 extra_params（含 disabled 参数）
        # 避免不支持 thinking 的模型在 fast 模式下漏掉 thinking.type=disabled 参数
        profile["thinking"] = None
    return profile


def _to_int(value: Any) -> Optional[int]:
    try:
        num = int(value)
        return num if num > 0 else None
    except Exception:
        return None


def _pick_dict(source: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    for key in keys:
        value = source.get(key)
        if isinstance(value, dict):
            return value
    return {}

