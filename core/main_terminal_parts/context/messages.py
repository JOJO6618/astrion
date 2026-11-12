import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from config import (
        OUTPUT_FORMATS, DATA_DIR, PROMPTS_DIR, NEED_CONFIRMATION,
        MAX_TERMINALS, TERMINAL_BUFFER_SIZE, TERMINAL_DISPLAY_SIZE,
        MAX_READ_FILE_CHARS, READ_TOOL_DEFAULT_MAX_CHARS,
        READ_TOOL_DEFAULT_CONTEXT_BEFORE, READ_TOOL_DEFAULT_CONTEXT_AFTER,
        READ_TOOL_MAX_CONTEXT_BEFORE, READ_TOOL_MAX_CONTEXT_AFTER,
        READ_TOOL_DEFAULT_MAX_MATCHES, READ_TOOL_MAX_MATCHES,
        READ_TOOL_MAX_FILE_SIZE,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_MODE,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        CUSTOM_TOOLS_ENABLED,
    )
except ImportError:
    import sys
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS, DATA_DIR, PROMPTS_DIR, NEED_CONFIRMATION,
        MAX_TERMINALS, TERMINAL_BUFFER_SIZE, TERMINAL_DISPLAY_SIZE,
        MAX_READ_FILE_CHARS, READ_TOOL_DEFAULT_MAX_CHARS,
        READ_TOOL_DEFAULT_CONTEXT_BEFORE, READ_TOOL_DEFAULT_CONTEXT_AFTER,
        READ_TOOL_MAX_CONTEXT_BEFORE, READ_TOOL_MAX_CONTEXT_AFTER,
        READ_TOOL_DEFAULT_MAX_MATCHES, READ_TOOL_MAX_MATCHES,
        READ_TOOL_MAX_FILE_SIZE,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_MODE,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        CUSTOM_TOOLS_ENABLED,
    )

from modules.file_manager import FileManager
from modules.search_engine import SearchEngine
from modules.terminal_ops import TerminalOperator
from modules.memory_manager import MemoryManager
from modules.terminal_manager import TerminalManager
from modules.todo_manager import TodoManager
from modules.sub_agent import SubAgentManager
from modules.webpage_extractor import extract_webpage_content, tavily_extract
from modules.ocr_client import OCRClient
from modules.easter_egg_manager import EasterEggManager
from modules.personalization_manager import (
    load_personalization_config,
    build_personalization_prompt,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_DEFAULT,
)
from modules.skills_manager import (
    get_skills_catalog,
    build_skills_list,
    merge_enabled_skills,
    build_skills_prompt,
    infer_private_skills_dir,
)
from modules.custom_tool_registry import CustomToolRegistry, build_default_tool_category
from modules.custom_tool_executor import CustomToolExecutor


from modules.container_monitor import collect_stats, inspect_state
from core.tool_config import TOOL_CATEGORIES
from utils.api_client import APIClient
from utils.context_manager import ContextManager, AUTO_SHALLOW_PLACEHOLDER
from utils.host_workspace_debug import write_host_workspace_debug
from utils.tool_result_formatter import format_tool_result_for_context
from utils.logger import setup_logger
from config.model_profiles import (
    get_model_profile,
    get_model_prompt_replacements,
    get_model_context_window,
    model_supports_image,
    model_supports_video,
)

logger = setup_logger(__name__)
DISABLE_LENGTH_CHECK = True


def _same_responses_provider(source_model_key: str, current_model_key: str) -> bool:
    """两个 model_key 是否同属一个 responses provider（加密 reasoning 块可回放的边界）。

    判定已提取到中性模块 ``utils/responses_reasoning.same_responses_provider``，
    Agent 与 Chat 共用（Chat 历史构建在 ``modules/chat_mode/context.py``）。
    双方都解析出非空 provider_id 且相等才放行；解析失败（模型已失效/未知）一律剥离，
    避免把不兼容的加密块发给错误上游。

    多连接/多账号（2026-10-29）：provider_id 已是「连接身份」（同一目录不同 Key =
    不同 provider_id，Codex 不同账号 = 不同连接 id），因此不同 Key/账号的加密推理
    天然隔离；responses_account_id 再比对一次作双保险。
    """
    from utils.responses_reasoning import same_responses_provider

    return same_responses_provider(source_model_key, current_model_key)


def _build_sub_md_notice(filename: str, sub_paths: List[str], total: int) -> str:
    """构建子目录指令文件路径通知文案（只列相对路径，不注入内容）。无子目录文件时返回空串。"""
    if not sub_paths:
        return ""
    lines = [f"另外，工作区以下子目录也存在 {filename} 规范文件：", ""]
    for p in sub_paths:
        lines.append(f"- `{p}`")
    if total > len(sub_paths):
        lines.append("")
        lines.append(f"...共 {total} 个")
    lines.append("")
    lines.append(f"这些子目录规范仅适用于其所在目录范围。若你的操作涉及上述目录，请先自行读取对应 {filename} 了解局部规范后再动手，不得将子目录规范当作全局规范套用。")
    return "\n".join(lines)


class MessagesMixin:
    """MainTerminalContextMixin messages 能力 mixin。"""

    def build_messages(self, context: Dict, user_input: str) -> List[Dict]:
            """构建消息列表（添加终端内容注入）"""
            try:
                file_tree_preview = (context.get("project_info", {}).get("file_tree") or "").splitlines()
                write_host_workspace_debug(
                    "main_terminal.build_messages.context_snapshot",
                    terminal_id=id(self),
                    terminal_project_path=str(getattr(self, "project_path", "")),
                    context_project_path=str(getattr(getattr(self, "context_manager", None), "project_path", "")),
                    project_info_path=str(context.get("project_info", {}).get("path", "")),
                    file_tree_first_line=file_tree_preview[0] if file_tree_preview else "",
                    current_conversation_id=getattr(getattr(self, "context_manager", None), "current_conversation_id", None),
                )
            except Exception:
                pass
            from modules.system_prompt_snapshot import SYSTEM_PROMPT_MARKER
            from .system_prompt import frozen_system_prompt

            messages = [{
                "role": "system",
                "content": frozen_system_prompt(self, context),
                SYSTEM_PROMPT_MARKER: True,
            }]
            personalization_config = getattr(self.context_manager, "custom_personalization_config", None) or load_personalization_config(self.data_dir)
            shallow_replace_enabled = bool(personalization_config.get("auto_shallow_compress_enabled", False)) if isinstance(personalization_config, dict) else False

            # 添加对话历史（保留完整结构，包括tool_calls和tool消息）
            conversation = context["conversation"]
            replaced_tool_count = 0
            deep_compacted_skipped = 0
            orphan_tool_skipped = 0
            # 已随 assistant 消息发出的 tool_call id 集合：用于剥离孤儿 tool 消息。
            # 防御线：历史数据一旦被并发写入/截断损坏（assistant.tool_calls 与其 tool
            # 响应错位），孤儿 tool 消息会让 API 直接 400（tool_call_id is not found），
            # 对话永久不可用。这里保证即使数据损坏，请求也始终合法（内容降级但不崩）。
            emitted_tool_call_ids = set()
            for idx, conv in enumerate(conversation):
                metadata = conv.get("metadata") or {}
                # 深压缩：被标记为 deep_compacted 的消息（整段已压缩前缀）原文保留在历史中用于展示，
                # 但构建请求时整体跳过，不纳入上下文。由于按连续前缀整体标记，
                # 不会出现 assistant.tool_calls 与其 tool 响应被拆散导致的配对悬空。
                if metadata.get("deep_compacted"):
                    deep_compacted_skipped += 1
                    continue
                if conv["role"] == "assistant":
                    # Assistant消息可能包含工具调用
                    message = {
                        "role": conv["role"],
                        "content": conv["content"]
                    }
                    # 对于思考模式（如 DeepSeek thinking），assistant 历史消息中的
                    # reasoning_content 即使为空字符串也需要原样回传，否则下一轮可能被
                    # API 判定为“未回传 reasoning_content”并返回 400。
                    if "reasoning_content" in conv:
                        message["reasoning_content"] = conv.get("reasoning_content", "")
                    # Responses 通道（通用）：加密 reasoning items 随消息透传给适配层回插上下文
                    # （仅 responses 协议模型会产生该 metadata；其他模型无此字段不受影响）
                    # 跨 provider 剥离：加密块由各自上游加密、互不兼容（ChatGPT 后端 /
                    # OpenAI API / xAI），provider 不同则不回插（对话仍能继续，仅丢
                    # 推理连续性——互切锁只按协议二分，responses 跨 provider 放行）
                    _responses_items = metadata.get("responses_reasoning_items")
                    if _responses_items:
                        _src_key = str(metadata.get("model_key") or "")
                        _cur_key = str(getattr(self, "model_key", None) or "")
                        if _same_responses_provider(_src_key, _cur_key):
                            message["responses_reasoning_items"] = _responses_items
                    # 如果有工具调用信息，添加到消息中
                    tool_calls = conv.get("tool_calls") or []
                    if tool_calls and self._tool_calls_followed_by_tools(conversation, idx, tool_calls):
                        message["tool_calls"] = tool_calls
                        for _tc in tool_calls:
                            _tcid = (_tc or {}).get("id")
                            if _tcid:
                                emitted_tool_call_ids.add(_tcid)
                    elif tool_calls:
                        # 悬空 tool_calls 被剥离（工具结果缺失：任务在工具执行中被
                        # 硬取消/进程中断）。若 assistant 内容为空，部分提供商会
                        # 400（assistant 内容不能为空），填占位文本保证请求合法。
                        if not str(message.get("content") or "").strip():
                            message["content"] = "（本轮工具调用被中断）"
                    messages.append(message)

                elif conv["role"] == "tool":
                    # 孤儿 tool 消息（父 assistant 被跳过/未携带 tool_calls/数据损坏）
                    # 直接剥离，避免 API 400；正常情况下不会发生。
                    _tool_call_id = conv.get("tool_call_id") or ""
                    if not _tool_call_id or _tool_call_id not in emitted_tool_call_ids:
                        orphan_tool_skipped += 1
                        continue
                    if shallow_replace_enabled and metadata.get("auto_shallow_compacted"):
                        messages.append({
                            "role": "tool",
                            "content": AUTO_SHALLOW_PLACEHOLDER,
                            "tool_call_id": conv.get("tool_call_id", ""),
                            "name": conv.get("name", "")
                        })
                        replaced_tool_count += 1
                        continue
                    # Tool消息需要保留完整结构
                    images = conv.get("images") or metadata.get("images") or []
                    videos = conv.get("videos") or metadata.get("videos") or []
                    media_refs = conv.get("media_refs") or metadata.get("media_refs") or []
                    content_value = conv.get("content")
                    if isinstance(content_value, list):
                        content_payload = content_value
                    elif images or videos or media_refs:
                        content_payload = self.context_manager._build_content_with_images(
                            content_value,
                            images,
                            videos,
                            media_refs=media_refs,
                        )
                    else:
                        content_payload = content_value
                    message = {
                        "role": "tool",
                        "content": content_payload,
                        "tool_call_id": conv.get("tool_call_id", ""),
                        "name": conv.get("name", "")
                    }
                    messages.append(message)

                else:
                    # User 或普通 System 消息
                    images = conv.get("images") or metadata.get("images") or []
                    videos = conv.get("videos") or metadata.get("videos") or []
                    media_refs = conv.get("media_refs") or metadata.get("media_refs") or []
                    content_payload = (
                        self.context_manager._build_content_with_images(
                            conv["content"],
                            images,
                            videos,
                            media_refs=media_refs,
                        )
                        if (images or videos or media_refs) and not metadata.get("media_paths_only") else conv["content"]
                    )
                    # 调试：记录所有 system 消息
                    if conv["role"] == "system":
                        logger.info(f"[DEBUG build_messages] 添加 system 消息: content前50字={conv['content'][:50]}")
                    # 调试：记录多智能体子智能体输出消息是否进入上下文
                    if metadata.get("multi_agent_message"):
                        logger.info(
                            f"[DEBUG build_messages] 添加多智能体子智能体消息到上下文: "
                            f"role={conv['role']}, subtype={metadata.get('multi_agent_subtype')}, "
                            f"display_name={metadata.get('multi_agent_display_name')}, "
                            f"content前80字={str(conv.get('content', ''))[:80]}"
                        )
                    messages.append({
                        "role": conv["role"],
                        "content": content_payload
                    })

            # 当前用户输入已经在conversation中了，不需要重复添加
            if shallow_replace_enabled:
                print(f"[ContextCompression] build_messages 替换tool占位符: {replaced_tool_count} 条")
            if deep_compacted_skipped:
                print(f"[ContextCompression] build_messages 跳过已深压缩消息: {deep_compacted_skipped} 条")
            if orphan_tool_skipped:
                logger.warning(
                    f"[messages] build_messages 剥离孤儿 tool 消息 {orphan_tool_skipped} 条 "
                    f"（对话历史疑似损坏，建议检查 tool_call 配对）"
                )
            return messages
