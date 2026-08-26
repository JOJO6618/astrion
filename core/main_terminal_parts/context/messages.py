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
from modules.multi_agent.prompts import build_available_agents_prompt
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
            # 根据当前模型多模态能力选择系统提示。system prompt 按对话冻结；
            # 工具 schema 保持动态，由 define_tools 每轮根据真实状态生成。
            current_model = getattr(self, "model_key", None)
            prompt_name = "main_system_vl" if (model_supports_image(current_model) or model_supports_video(current_model)) else "main_system"
            model_key = getattr(self, "model_key", None)
            prompt_replacements = get_model_prompt_replacements(model_key)

            is_multi_agent_mode = bool(getattr(self, "multi_agent_mode", False))

            def _build_main_system_prompt() -> str:
                if is_multi_agent_mode:
                    # 多智能体模式下主 prompt 使用 Team Leader 专属模板
                    try:
                        from modules.multi_agent.prompts import _load_template
                        template = _load_template("master")
                        # master.txt 含 HTML 示例等单花括号内容，不能走 str.format；
                        # 仅替换模型描述占位符，避免字面量泄漏
                        return template.replace(
                            "{model_description}",
                            prompt_replacements.get("model_description", ""),
                        )
                    except Exception as exc:
                        logger.warning(f"[messages] 加载多智能体主 prompt 失败: {exc}")
                system_prompt_template = self.load_prompt(prompt_name)
                # main_system.txt / main_system_vl.txt 仅使用 {model_description}
                base_prompt = system_prompt_template.format(
                    model_description=prompt_replacements.get("model_description", "")
                )
                # 修改留痕附言：开关开启且 conversation_id 可用时追加独立 prompt 段落
                # （含本对话专属留痕目录；冻结在 prompt 中，与该对话终身一致）
                try:
                    from modules.modify_history import build_modify_history_prompt_note
                    note = build_modify_history_prompt_note(
                        project_path=getattr(self, "project_path", None),
                        data_dir=getattr(self, "data_dir", None),
                        conversation_id=getattr(getattr(self, "context_manager", None), "current_conversation_id", None),
                    )
                    if note:
                        base_prompt = f"{base_prompt}\n\n{note}"
                except Exception as exc:
                    logger.warning(f"[messages] 构建修改留痕 prompt 失败: {exc}")
                return base_prompt

            main_system_frozen_key = (
                "frozen_main_system_prompt_multi_agent"
                if is_multi_agent_mode
                else "frozen_main_system_prompt"
            )
            system_prompt = self._get_or_init_frozen_prompt(
                main_system_frozen_key,
                _build_main_system_prompt,
            )

            messages = [
                {"role": "system", "content": system_prompt}
            ]

            personalization_config = getattr(self.context_manager, "custom_personalization_config", None) or load_personalization_config(self.data_dir)
            shallow_replace_enabled = bool(personalization_config.get("auto_shallow_compress_enabled", False)) if isinstance(personalization_config, dict) else False

            # 顺序：主prompt → 权限模式 → 执行环境 → 最近对话 → 个性化配置 → 工作区信息 → AGENTS.md → CLAUDE.md → skills → 记忆 → 自定义 → 禁用提示

            # 权限模式
            permission_mode_message = self._get_or_init_frozen_mode_prompt(
                "frozen_permission_prompt",
                self._build_permission_mode_message,
            )
            if permission_mode_message:
                messages.append({"role": "system", "content": permission_mode_message})

            # 执行环境
            execution_mode_message = self._get_or_init_frozen_mode_prompt(
                "frozen_execution_prompt",
                self._build_execution_mode_message,
            )
            if execution_mode_message:
                messages.append({"role": "system", "content": execution_mode_message})

            # 运行模式（计划/询问/执行）
            work_mode_message = self._get_or_init_frozen_mode_prompt(
                "frozen_work_mode_prompt",
                self._build_work_mode_message,
            )
            if work_mode_message:
                messages.append({"role": "system", "content": work_mode_message})

            # 最近对话
            def _build_recent_conversations_prompt() -> str:
                recent_conversations_enabled = (
                    bool(personalization_config.get("recent_conversations_prompt_enabled", False))
                    if isinstance(personalization_config, dict)
                    else False
                )
                if not recent_conversations_enabled:
                    return ""
                try:
                    recent_limit = int(
                        personalization_config.get(
                            "recent_conversations_prompt_limit",
                            RECENT_CONVERSATIONS_PROMPT_LIMIT_DEFAULT,
                        )
                    )
                except Exception:
                    recent_limit = RECENT_CONVERSATIONS_PROMPT_LIMIT_DEFAULT
                recent_limit = max(
                    RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN,
                    min(RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX, recent_limit),
                )
                return self._build_recent_conversations_message(limit=recent_limit) or ""

            recent_conversations_prompt = self._get_or_init_frozen_prompt(
                "frozen_recent_conversations_prompt",
                _build_recent_conversations_prompt,
            )
            if recent_conversations_prompt:
                messages.append({"role": "system", "content": recent_conversations_prompt})

            # 个性化配置
            def _build_personalization_system_prompt() -> str:
                personalization_block = build_personalization_prompt(personalization_config, include_header=False)
                if not personalization_block:
                    return ""
                personalization_template = self.load_prompt("personalization").strip()
                if personalization_template and "{personalization_block}" in personalization_template:
                    return personalization_template.format(personalization_block=personalization_block)
                if personalization_template:
                    return f"{personalization_template}\n{personalization_block}"
                return personalization_block

            personalization_text = self._get_or_init_frozen_prompt(
                "frozen_personalization_prompt",
                _build_personalization_system_prompt,
            )
            if personalization_text:
                messages.append({"role": "system", "content": personalization_text})

            # 工作区信息
            workspace_system = self._get_or_init_frozen_prompt(
                "frozen_workspace_prompt",
                lambda: self.context_manager._build_workspace_system_message(context) or "",
            )
            if workspace_system:
                messages.append({"role": "system", "content": workspace_system})

            # AGENTS.md
            def _build_agents_md_prompt() -> str:
                agents_md_inject_enabled = bool(personalization_config.get("agents_md_auto_inject", False)) if isinstance(personalization_config, dict) else False
                if not agents_md_inject_enabled:
                    return ""
                agents_md_content = self._load_agents_md_content()
                if not agents_md_content:
                    return ""
                # 子目录 AGENTS.md 只通知相对路径，不注入内容
                sub_paths, sub_total = self._load_sub_agents_md_paths()
                sub_notice = _build_sub_md_notice("AGENTS.md", sub_paths, sub_total)
                agents_md_template = self.load_prompt("agents_md_inject").strip()
                if agents_md_template and "{{AGENTS_MD_CONTENT}}" in agents_md_template:
                    text = agents_md_template.replace("{{AGENTS_MD_CONTENT}}", agents_md_content)
                    text = text.replace("{{AGENTS_MD_UPDATED_AT}}", self._load_agents_md_updated_at())
                    text = text.replace("{{SUB_AGENTS_MD_NOTICE}}", sub_notice)
                    return text
                # 模板缺失/无占位符时的兜底格式
                text = f"【AGENTS.md 项目规范】{self._load_agents_md_updated_at()}\n\n{agents_md_content}\n\n---\n请注意：以上规范来自工作区根目录的 AGENTS.md 文件，若有冲突请以 AGENTS.md 为准。"
                return f"{text}\n\n{sub_notice}" if sub_notice else text

            # AGENTS.md 注入开关可能变化，只在开启时生成并缓存，避免关闭时把空字符串冻住
            agents_md_inject_enabled = (
                bool(personalization_config.get("agents_md_auto_inject", False))
                if isinstance(personalization_config, dict)
                else False
            )
            if agents_md_inject_enabled:
                agents_md_text = self._get_or_init_frozen_prompt(
                    "frozen_agents_md_prompt",
                    _build_agents_md_prompt,
                )
                if agents_md_text:
                    messages.append({"role": "system", "content": agents_md_text})

            # CLAUDE.md（默认关闭，开启后与 AGENTS.md 并列注入）
            def _build_claude_md_prompt() -> str:
                claude_md_content = self._load_root_md_content("CLAUDE.md")
                if not claude_md_content:
                    return ""
                # 子目录 CLAUDE.md 只通知相对路径，不注入内容
                sub_paths, sub_total = self._load_sub_md_paths("CLAUDE.md")
                sub_notice = _build_sub_md_notice("CLAUDE.md", sub_paths, sub_total)
                claude_md_template = self.load_prompt("claude_md_inject").strip()
                if claude_md_template and "{{CLAUDE_MD_CONTENT}}" in claude_md_template:
                    text = claude_md_template.replace("{{CLAUDE_MD_CONTENT}}", claude_md_content)
                    text = text.replace("{{CLAUDE_MD_UPDATED_AT}}", self._load_root_md_updated_at("CLAUDE.md"))
                    text = text.replace("{{SUB_CLAUDE_MD_NOTICE}}", sub_notice)
                    return text
                # 模板缺失/无占位符时的兜底格式
                text = f"【CLAUDE.md 项目规范】{self._load_root_md_updated_at('CLAUDE.md')}\n\n{claude_md_content}\n\n---\n请注意：以上规范来自工作区根目录的 CLAUDE.md 文件，若有冲突请以 CLAUDE.md 为准。"
                return f"{text}\n\n{sub_notice}" if sub_notice else text

            # 与 AGENTS.md 同理，只在开启时生成并缓存
            claude_md_inject_enabled = (
                bool(personalization_config.get("claude_md_auto_inject", False))
                if isinstance(personalization_config, dict)
                else False
            )
            if claude_md_inject_enabled:
                claude_md_text = self._get_or_init_frozen_prompt(
                    "frozen_claude_md_prompt",
                    _build_claude_md_prompt,
                )
                if claude_md_text:
                    messages.append({"role": "system", "content": claude_md_text})

            # skills 列表
            skills_catalog = get_skills_catalog(
                private_dir=infer_private_skills_dir(self.data_dir),
                project_path=self.project_path,
                scan_project_agents=(
                    bool(personalization_config.get("agents_skills_scan_enabled", True))
                    if isinstance(personalization_config, dict)
                    else True
                ),
            )
            enabled_skills = merge_enabled_skills(
                personalization_config.get("enabled_skills") if isinstance(personalization_config, dict) else None,
                skills_catalog,
                personalization_config.get("skills_catalog_snapshot") if isinstance(personalization_config, dict) else None,
            )
            def _build_skills_system_prompt() -> str:
                skills_template = self.load_prompt("skills_system").strip()
                skills_list = build_skills_list(skills_catalog, enabled_skills)
                return build_skills_prompt(skills_template, skills_list) or ""

            skills_prompt = self._get_or_init_frozen_prompt(
                "frozen_skills_prompt",
                _build_skills_system_prompt,
            )
            if skills_prompt:
                messages.append({"role": "system", "content": skills_prompt})

            # 工作流（Workflow）上下文：不冻结，每次按当前状态现生成。
            # 阶段推进时由 workflow_flow.refresh_workflow_system_segment 同步刷新，
            # 压缩不影响（system 段不在压缩范围），天然免疫压缩丢失。
            def _build_workflow_system_prompt() -> str:
                try:
                    from server.workflow_flow import build_workflow_system_prompt
                    conversation_id = getattr(self.context_manager, "current_conversation_id", None)
                    return build_workflow_system_prompt(data_dir=self.data_dir, conversation_id=conversation_id) or ""
                except Exception:
                    return ""

            workflow_prompt = _build_workflow_system_prompt()
            if workflow_prompt:
                messages.append({"role": "system", "content": workflow_prompt})

            # 记忆系统（总体长期记忆 + 项目记忆）
            def _build_memory_system_prompt() -> str:
                return self._build_memory_system_content()

            memory_prompt = self._get_or_init_frozen_prompt(
                "frozen_memory_prompt",
                _build_memory_system_prompt,
            )
            if memory_prompt:
                messages.append({"role": "system", "content": memory_prompt})

            # API 自定义 system prompt
            custom_system_prompt = self._get_or_init_frozen_prompt(
                "frozen_custom_system_prompt",
                lambda: (
                    getattr(self.context_manager, "custom_system_prompt", "").strip()
                    if isinstance(getattr(self.context_manager, "custom_system_prompt", None), str)
                    else ""
                ),
            )
            if custom_system_prompt:
                messages.append({"role": "system", "content": custom_system_prompt})

            # 禁用工具提示
            disabled_notice = self._get_or_init_frozen_prompt(
                "frozen_disabled_tools_prompt",
                lambda: self._format_disabled_tool_notice() or "",
            )

            # 多智能体模式额外提示词：可用的子智能体角色（动态 prompt，第一个用户消息后冻结）
            if getattr(self, "multi_agent_mode", False):
                try:
                    available_agents_prompt = self._get_or_init_frozen_prompt(
                        "frozen_available_agents_prompt",
                        lambda: build_available_agents_prompt() or "",
                    )
                    if available_agents_prompt:
                        messages.append({"role": "system", "content": available_agents_prompt})
                except Exception as exc:
                    logger.warning(f"[messages] 注入可用子智能体 prompt 失败: {exc}")
            if disabled_notice:
                messages.append({
                    "role": "system",
                    "content": disabled_notice
                })

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
                    # 如果有工具调用信息，添加到消息中
                    tool_calls = conv.get("tool_calls") or []
                    if tool_calls and self._tool_calls_followed_by_tools(conversation, idx, tool_calls):
                        message["tool_calls"] = tool_calls
                        for _tc in tool_calls:
                            _tcid = (_tc or {}).get("id")
                            if _tcid:
                                emitted_tool_call_ids.add(_tcid)
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
                        if (images or videos or media_refs) else conv["content"]
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
