import asyncio
import json
from datetime import datetime
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
)
from modules.skills_manager import (
    get_skills_catalog,
    build_skills_list,
    merge_enabled_skills,
    build_skills_prompt,
)
from modules.custom_tool_registry import CustomToolRegistry, build_default_tool_category
from modules.custom_tool_executor import CustomToolExecutor

try:
    from config.limits import REASONING_EFFORT_LEVELS
except ImportError:
    REASONING_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

from modules.container_monitor import collect_stats, inspect_state
from core.tool_config import TOOL_CATEGORIES
from utils.api_client import APIClient
from utils.context_manager import ContextManager
from utils.tool_result_formatter import (
    extract_mcp_content_for_context,
    format_tool_result_for_context,
)
from utils.logger import setup_logger
from config.model_profiles import (
    get_model_profile,
    get_model_prompt_replacements,
    get_model_context_window,
    resolve_model_key,
    model_supports_image,
    model_supports_video,
)

from modules.i18n import tr

logger = setup_logger(__name__)
DISABLE_LENGTH_CHECK = True

class MainTerminalCommandMixin:
    async def run(self):
            """运行主终端循环"""
            print(f"\n{OUTPUT_FORMATS['info']} 主终端已启动")
            print(f"{OUTPUT_FORMATS['info']} 当前对话: {self.context_manager.current_conversation_id}")

            while True:
                try:
                    # 获取用户输入（使用人的表情）
                    user_input = input("\n👤 > ").strip()

                    if not user_input:
                        continue

                    # 处理命令（命令不记录到对话历史）
                    if user_input.startswith('/'):
                        await self.handle_command(user_input[1:])
                    elif user_input.lower() in ['exit', 'quit', 'q']:
                        # 用户可能忘记加斜杠
                        print(f"{OUTPUT_FORMATS['info']} 提示: 使用 /exit 退出系统")
                        continue
                    elif user_input.lower() == 'help':
                        print(f"{OUTPUT_FORMATS['info']} 提示: 使用 /help 查看帮助")
                        continue
                    else:
                        # 确保有活动对话
                        self._ensure_conversation()

                        # 只有非命令的输入才记录到对话历史
                        self.context_manager.add_conversation("user", user_input)

                        # 新增：开始新的任务会话
                        self.current_session_id += 1

                        # AI回复前空一行，并显示机器人图标
                        print("\n🤖 >", end=" ")
                        await self.handle_task(user_input)
                        # 回复后自动空一行（在handle_task完成后）

                except KeyboardInterrupt:
                    print(f"\n{OUTPUT_FORMATS['warning']} 使用 /exit 退出系统")
                    continue
                except Exception as e:
                    logger.error(f"主终端错误: {e}", exc_info=True)
                    print(f"{OUTPUT_FORMATS['error']} 发生错误: {e}")
                    # 错误后仍然尝试自动保存
                    try:
                        self.context_manager.auto_save_conversation()
                    except:
                        pass

    async def handle_command(self, command: str):
            """处理系统命令"""
            parts = command.split(maxsplit=1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if cmd in self.commands:
                await self.commands[cmd](args)
            else:
                print(f"{OUTPUT_FORMATS['error']} {tr('commands.unknown_command', cmd=cmd)}")
                await self.show_help()

    async def handle_task(self, user_input: str):
            """处理用户任务（完全修复版：彻底解决对话记录重复问题）"""
            try:
                # 新增：开始新的任务会话
                self.current_session_id += 1

                # === 上下文预算与安全校验 ===
                current_tokens = self.context_manager.get_current_context_tokens()
                max_context_tokens = get_model_context_window(self.model_key)
                if max_context_tokens:
                    if current_tokens >= max_context_tokens:
                        msg = tr(
                            "commands.context_over_limit",
                            current_tokens=current_tokens,
                            max_context_tokens=max_context_tokens,
                        )
                        print(f"{OUTPUT_FORMATS['error']} {msg}")
                        # 记录一条系统消息，方便回溯
                        self.context_manager.add_conversation("system", msg)
                        return
                    usage_percent = (current_tokens / max_context_tokens) * 100
                    warned = self.context_manager.conversation_metadata.get("context_warning_sent", False)
                    if usage_percent >= 70 and not warned:
                        warn_msg = tr(
                            "commands.context_warning",
                            usage_percent=usage_percent,
                            current_tokens=current_tokens,
                            max_context_tokens=max_context_tokens,
                        )
                        print(f"{OUTPUT_FORMATS['warning']} {warn_msg}")
                        self.context_manager.conversation_metadata["context_warning_sent"] = True
                        self.context_manager.auto_save_conversation(force=True)
                # 将上下文预算传给API客户端，动态调整 max_tokens
                self.api_client.update_context_budget(current_tokens, max_context_tokens)

                # 构建上下文
                context = self.build_context()

                # 构建消息
                messages = self.build_messages(context, user_input)

                # 定义可用工具
                tools = self.define_tools()

                # 用于收集本次任务的所有信息（关键：不立即保存到对话历史）
                collected_tool_calls = []
                collected_tool_results = []
                final_response = ""
                final_thinking = ""

                # 工具处理器：只执行工具，收集信息，绝不保存到对话历史
                async def tool_handler(tool_name: str, arguments: Dict) -> str:
                    # 执行工具调用
                    result = await self.handle_tool_call(tool_name, arguments)

                    # 生成工具调用ID
                    tool_call_id = f"call_{datetime.now().timestamp()}_{tool_name}"

                    # 收集工具调用信息（不保存）
                    tool_call_info = {
                        "id": tool_call_id,
                        "type": "function", 
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(arguments, ensure_ascii=False)
                        }
                    }
                    collected_tool_calls.append(tool_call_info)

                    # 处理工具结果用于保存
                    try:
                        parsed = json.loads(result)
                        result_data = parsed if isinstance(parsed, dict) else {}
                    except Exception:
                        result_data = {}
                    tool_metadata = {"tool_payload": result_data} if result_data else None
                    if isinstance(tool_name, str) and tool_name.startswith("mcp__") and isinstance(result_data, dict):
                        mcp_parsed = extract_mcp_content_for_context(result_data)
                        tool_result_content = (
                            mcp_parsed.get("text")
                            or format_tool_result_for_context(tool_name, result_data, result)
                        )
                        tool_metadata = {"tool_payload": mcp_parsed.get("sanitized_payload") or result_data}
                        mcp_media_items = mcp_parsed.get("media_items") or []
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
                    else:
                        tool_result_content = format_tool_result_for_context(tool_name, result_data, result)
                        tool_media_refs = None
                    tool_images = None
                    tool_videos = None
                    if (
                        isinstance(result_data, dict)
                        and result_data.get("success") is not False
                    ):
                        if tool_name == "view_image":
                            img_path = result_data.get("path")
                            if img_path:
                                tool_images = [img_path]
                        elif tool_name == "view_video":
                            video_path = result_data.get("path")
                            if video_path:
                                tool_videos = [video_path]

                    # 收集工具结果（不保存）
                    collected_tool_results.append({
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": tool_result_content,
                        "system_message": result_data.get("system_message") if isinstance(result_data, dict) else None,
                        "task_id": result_data.get("task_id") if isinstance(result_data, dict) else None,
                        "raw_result_data": result_data if result_data else None,
                        "metadata": tool_metadata,
                        "images": tool_images,
                        "videos": tool_videos,
                        "media_refs": tool_media_refs,
                    })

                    return result

                # 调用带工具的API（模型自己决定是否使用工具）
                response = await self.api_client.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    tool_handler=tool_handler
                )

                # 保存响应内容
                final_response = response

                # 获取思考内容（如果有的话）：取最后一条 assistant 消息的 reasoning_content
                if not final_thinking:
                    for _msg in reversed(messages):
                        if _msg.get("role") == "assistant" and _msg.get("reasoning_content"):
                            final_thinking = _msg["reasoning_content"]
                            break

                # ===== 统一保存到对话历史（关键修复） =====

                # 1. 构建助手回复内容（思考内容通过 reasoning_content 存储）
                assistant_content = final_response or tr("commands.task_done_placeholder")

                # 2. 保存assistant消息（包含tool_calls但不包含结果）
                self.context_manager.add_conversation(
                    "assistant",
                    assistant_content,
                    collected_tool_calls if collected_tool_calls else None,
                    reasoning_content=final_thinking or None
                )

                # 3. 保存独立的tool消息
                for tool_result in collected_tool_results:
                    self.context_manager.add_conversation(
                        "tool",
                        tool_result["content"],
                        tool_call_id=tool_result["tool_call_id"],
                        name=tool_result["name"],
                        metadata=tool_result.get("metadata"),
                        images=tool_result.get("images"),
                        videos=tool_result.get("videos"),
                        media_refs=tool_result.get("media_refs"),
                    )
                    system_message = tool_result.get("system_message")
                    if system_message:
                        from server.chat_flow_task_support import inject_runtime_user_message
                        inject_runtime_user_message(
                            web_terminal=self,
                            messages=None,
                            text=system_message,
                            source="sub_agent",
                            sender=None,
                            conversation_id=getattr(self.context_manager, "current_conversation_id", None),
                            inline=False,
                            extra_metadata={"task_id": tool_result.get("task_id")},
                        )

                # 4. 在终端显示执行信息（不保存到历史）
                if collected_tool_calls:
                    tool_names = [tc['function']['name'] for tc in collected_tool_calls]

                    for tool_name in tool_names:
                        if tool_name == "create_file":
                            print(f"{OUTPUT_FORMATS['file']} 创建文件")
                        elif tool_name == "read_file":
                            print(f"{OUTPUT_FORMATS['file']} 读取文件")
                        elif tool_name in {"vlm_analyze", "ocr_image"}:
                            print(f"{OUTPUT_FORMATS['file']} VLM 视觉理解")
                        elif tool_name == "write_file":
                            print(f"{OUTPUT_FORMATS['file']} 写入文件")
                        elif tool_name == "edit_file":
                            print(f"{OUTPUT_FORMATS['file']} 编辑文件")
                        elif tool_name == "delete_file":
                            print(f"{OUTPUT_FORMATS['file']} 删除文件")
                        elif tool_name == "terminal_session":
                            print(f"{OUTPUT_FORMATS['session']} 终端会话操作")
                        elif tool_name == "terminal_input":
                            print(f"{OUTPUT_FORMATS['terminal']} 执行终端命令")
                        elif tool_name == "web_search":
                            print(f"{OUTPUT_FORMATS['search']} 网络搜索")
                        elif tool_name == "run_command":
                            print(f"{OUTPUT_FORMATS['terminal']} 执行系统命令")
                        elif tool_name == "update_memory":
                            print(f"{OUTPUT_FORMATS['memory']} 更新记忆")
                        elif tool_name == "sleep":
                            print(f"{OUTPUT_FORMATS['info']} 等待操作")
                        else:
                            print(f"{OUTPUT_FORMATS['action']} 执行: {tool_name}")

                    if len(tool_names) > 1:
                        print(f"{OUTPUT_FORMATS['info']} 共执行 {len(tool_names)} 个操作")

            except Exception as e:
                logger.error(f"任务处理错误: {e}", exc_info=True)
                print(f"{OUTPUT_FORMATS['error']} 任务处理失败: {e}")
                # 错误时也尝试自动保存
                try:
                    self.context_manager.auto_save_conversation()
                except:
                    pass

    async def show_conversations(self, args: str = ""):
            """显示对话列表"""
            try:
                limit = 10  # 默认显示最近10个对话
                if args:
                    try:
                        limit = int(args)
                        limit = max(1, min(limit, 50))  # 限制在1-50之间
                    except ValueError:
                        print(f"{OUTPUT_FORMATS['warning']} {tr('commands.invalid_count_default')}")
                        limit = 10

                conversations = self.context_manager.get_conversation_list(limit=limit)

                if not conversations["conversations"]:
                    print(f"{OUTPUT_FORMATS['info']} {tr('commands.no_conversations')}")
                    return

                print(f"\n📚 最近 {len(conversations['conversations'])} 个对话:")
                print("="*70)

                for i, conv in enumerate(conversations["conversations"], 1):
                    # 状态图标
                    status_icon = "🟢" if conv["status"] == "active" else "📦" if conv["status"] == "archived" else "❌"

                    # 当前对话标记
                    current_mark = " [当前]" if conv["id"] == self.context_manager.current_conversation_id else ""

                    # 思考模式标记
                    mode_mark = "💭" if conv["thinking_mode"] else "⚡"

                    print(f"{i:2d}. {status_icon} {conv['id'][:16]}...{current_mark}")
                    print(f"    {mode_mark} {conv['title'][:50]}{'...' if len(conv['title']) > 50 else ''}")
                    print(f"    📅 {conv['updated_at'][:19]} | 💬 {conv['total_messages']} 条消息 | 🔧 {conv['total_tools']} 个工具")
                    print(f"    📁 {conv['project_path']}")
                    print()

                print(f"总计: {conversations['total']} 个对话")
                if conversations["has_more"]:
                    print(f"使用 /conversations {limit + 10} 查看更多")

            except Exception as e:
                print(f"{OUTPUT_FORMATS['error']} {tr('commands.conversations_list_failed', error=e)}")

    async def load_conversation_command(self, args: str):
            """加载指定对话"""
            if not args:
                print(f"{OUTPUT_FORMATS['error']} {tr('commands.load_need_id')}")
                print(tr("commands.load_usage_hint"))
                await self.show_conversations("5")  # 显示最近5个对话作为提示
                return

            conversation_id = args.strip()

            try:
                success = self.context_manager.load_conversation_by_id(conversation_id)
                if success:
                    print(f"{OUTPUT_FORMATS['success']} {tr('commands.conversation_loaded', conversation_id=conversation_id)}")
                    print(f"{OUTPUT_FORMATS['info']} {tr('commands.messages_count', count=len(self.context_manager.conversation_history))}")

                    self.current_session_id += 1

                else:
                    print(f"{OUTPUT_FORMATS['error']} {tr('commands.conversation_load_failed')}")

            except Exception as e:
                print(f"{OUTPUT_FORMATS['error']} {tr('commands.conversation_load_exception', error=e)}")

    async def new_conversation_command(self, args: str = ""):
            """创建新对话"""
            try:
                conversation_id = self.context_manager.start_new_conversation(
                    project_path=self.project_path,
                    thinking_mode=self.thinking_mode
                )

                print(f"{OUTPUT_FORMATS['success']} {tr('commands.conversation_created', conversation_id=conversation_id)}")

                self.current_session_id += 1

            except Exception as e:
                print(f"{OUTPUT_FORMATS['error']} {tr('commands.conversation_create_failed', error=e)}")

    async def save_conversation_command(self, args: str = ""):
            """手动保存当前对话"""
            try:
                success = self.context_manager.save_current_conversation()
                if success:
                    print(f"{OUTPUT_FORMATS['success']} {tr('commands.conversation_saved')}")
                else:
                    print(f"{OUTPUT_FORMATS['error']} {tr('commands.conversation_save_failed')}")
            except Exception as e:
                print(f"{OUTPUT_FORMATS['error']} {tr('commands.conversation_save_exception', error=e)}")

    async def clear_conversation(self, args: str = ""):
            """清除对话记录（修改版：创建新对话而不是清空）"""
            if input("确认创建新对话? 当前对话将被保存 (y/n): ").lower() == 'y':
                try:
                    # 保存当前对话
                    if self.context_manager.current_conversation_id:
                        self.context_manager.save_current_conversation()

                    # 创建新对话
                    await self.new_conversation_command()

                    print(f"{OUTPUT_FORMATS['success']} {tr('commands.new_conversation_started')}")

                except Exception as e:
                    print(f"{OUTPUT_FORMATS['error']} {tr('commands.conversation_create_failed', error=e)}")

    async def show_status(self, args: str = ""):
            """显示系统状态"""
            # 上下文状态
            context_status = self.context_manager.check_context_size()

            # 记忆状态
            memory_stats = self.memory_manager.get_memory_stats()

            # 文件结构
            structure = self.context_manager.get_project_structure()

            # 终端会话状态
            terminal_status = self.terminal_manager.list_terminals()

            # 思考模式状态
            thinking_status = self.get_run_mode_label()

            # 新增：对话统计
            conversation_stats = self.context_manager.get_conversation_statistics()

            status_text = f"""
    📊 系统状态:
      项目路径: {self.project_path}
      运行模式: {thinking_status}
      当前对话: {self.context_manager.current_conversation_id or '无'}

      上下文使用: {context_status['usage_percent']:.1f}%
      当前消息: {len(self.context_manager.conversation_history)} 条
      终端会话: {terminal_status['total']}/{terminal_status['max_allowed']} 个
      当前会话ID: {self.current_session_id}

      项目文件: {structure['total_files']} 个
      项目大小: {structure['total_size'] / 1024 / 1024:.2f} MB

      对话总数: {conversation_stats.get('total_conversations', 0)} 个
      历史消息: {conversation_stats.get('total_messages', 0)} 条
      工具调用: {conversation_stats.get('total_tools', 0)} 次

      主记忆: {memory_stats['main_memory']['lines']} 行
      任务记忆: {memory_stats['task_memory']['lines']} 行
    """
            container_report = self._container_status_report()
            if container_report:
                status_text += container_report
            print(status_text)

    def _container_status_report(self) -> str:
            session = getattr(self, "container_session", None)
            if not session or session.mode != "docker":
                return ""
            stats = collect_stats(session.container_name, session.sandbox_bin)
            state = inspect_state(session.container_name, session.sandbox_bin)
            lines = [f"  容器: {session.container_name or '未知'}"]
            if stats:
                cpu = stats.get("cpu_percent")
                mem = stats.get("memory", {})
                net = stats.get("net_io", {})
                block = stats.get("block_io", {})
                lines.append(f"    CPU: {cpu:.2f}%" if cpu is not None else "    CPU: 未知")
                if mem:
                    used = mem.get("used_bytes")
                    limit = mem.get("limit_bytes")
                    percent = mem.get("percent")
                    mem_line = "    内存: "
                    if used is not None:
                        mem_line += f"{used / (1024 * 1024):.2f}MB"
                    if limit:
                        mem_line += f" / {limit / (1024 * 1024):.2f}MB"
                    if percent is not None:
                        mem_line += f" ({percent:.2f}%)"
                    lines.append(mem_line)
                if net:
                    rx = net.get("rx_bytes") or 0
                    tx = net.get("tx_bytes") or 0
                    lines.append(f"    网络: ↓{rx/1024:.1f}KB ↑{tx/1024:.1f}KB")
                if block:
                    read = block.get("read_bytes") or 0
                    write = block.get("write_bytes") or 0
                    lines.append(f"    磁盘: 读 {read/1024:.1f}KB / 写 {write/1024:.1f}KB")
            else:
                lines.append("    指标: 暂无")
            if state:
                lines.append(f"    状态: {state.get('status')}")
            return "\n".join(lines) + "\n"

    async def save_state(self):
            """保存状态"""
            try:
                # 保存对话历史（使用新的持久化系统）
                self.context_manager.save_current_conversation()

                # 保存文件备注
                self.context_manager.save_annotations()

                print(f"{OUTPUT_FORMATS['success']} {tr('commands.state_saved')}")
            except Exception as e:
                print(f"{OUTPUT_FORMATS['error']} {tr('commands.state_save_failed', error=e)}")

    async def show_help(self, args: str = ""):
            """显示帮助信息"""
            # 根据当前模式显示不同的帮助信息
            mode_info = ""
            if self.thinking_mode:
                mode_info = "\n💡 思考模式:\n  - 每个新任务首次调用深度思考\n  - 同一任务后续调用快速响应\n  - 每个新任务都会重新思考"
            else:
                mode_info = "\n⚡ 快速模式:\n  - 不进行思考，直接响应\n  - 适合简单任务和快速交互"

            help_text = f"""
    📚 可用命令:
      /help         - 显示此帮助信息
      /exit         - 退出系统
      /status       - 显示系统状态
      /memory       - 管理记忆
      /clear        - 创建新对话
      /history      - 显示对话历史
      /files        - 显示项目文件
      /focused      - 显示聚焦文件
      /terminals    - 显示终端会话
      /mode         - 切换运行模式

    🗂️ 对话管理:
      /conversations [数量]  - 显示对话列表
      /load <对话ID>        - 加载指定对话
      /new                  - 创建新对话
      /save                 - 手动保存当前对话

    💡 使用提示:
      - 直接输入任务描述，系统会自动判断是否需要执行
      - 使用 Ctrl+C 可以中断当前操作
      - 重要操作会要求确认
      - 所有对话都会自动保存，不用担心丢失

    🔍 文件聚焦功能:
      - 系统可以聚焦最多3个文件，实现"边看边改"
      - 聚焦的文件内容会持续显示在上下文中
      - 适合需要频繁查看和修改的文件

    📺 持久化终端:
      - 可以打开最多3个终端会话
      - 终端保持运行状态，支持交互式程序
      - 使用 terminal_session 和 terminal_input 工具控制{mode_info}
    """
            print(help_text)

    async def show_terminals(self, args: str = ""):
            """显示终端会话列表"""
            result = self.terminal_manager.list_terminals()

            if result["total"] == 0:
                print(f"{OUTPUT_FORMATS['info']} {tr('commands.no_active_terminals')}")
            else:
                print(f"\n📺 终端会话列表 ({result['total']}/{result['max_allowed']}):")
                print("="*50)
                for session in result["sessions"]:
                    status_icon = "🟢" if session["is_running"] else "🔴"
                    active_mark = " [活动]" if session["is_active"] else ""
                    print(f"  {status_icon} {session['session_name']}{active_mark}")
                    print(f"     工作目录: {session['working_dir']}")
                    print(f"     Shell: {session['shell']}")
                    print(f"     运行时间: {session['uptime_seconds']:.1f}秒")
                    if session["is_interactive"]:
                        print(f"     ⚠️ 等待输入")
                print("="*50)

    async def exit_system(self, args: str = ""):
            """退出系统"""
            print(f"{OUTPUT_FORMATS['info']} {tr('commands.exiting')}")

            # 关闭所有终端会话
            self.terminal_manager.close_all()

            # 保存状态
            await self.save_state()

            exit(0)

    async def manage_memory(self, args: str = ""):
            """管理记忆"""
            if not args:
                print("""
    🧠 记忆管理:
      /memory show [main|task]  - 显示记忆内容
      /memory edit [main|task]  - 编辑记忆
      /memory clear task        - 清空任务记忆
      /memory merge             - 合并任务记忆到主记忆
      /memory backup [main|task]- 备份记忆
    """)
                return

            parts = args.split()
            action = parts[0] if parts else ""
            target = parts[1] if len(parts) > 1 else "main"

            if action == "show":
                if target == "main":
                    content = self.memory_manager.read_main_memory()
                else:
                    content = self.memory_manager.read_task_memory()
                print(f"\n{'='*50}")
                print(content)
                print('='*50)

            elif action == "clear" and target == "task":
                if input("确认清空任务记忆? (y/n): ").lower() == 'y':
                    self.memory_manager.clear_task_memory()

            elif action == "merge":
                self.memory_manager.merge_memories()

            elif action == "backup":
                path = self.memory_manager.backup_memory(target)
                if path:
                    print(tr("commands.memory_backup_saved", path=path))

    async def show_history(self, args: str = ""):
            """显示对话历史"""
            history = self.context_manager.conversation_history[-2000:]  # 最近2000条

            print("\n📜 对话历史:")
            print("="*50)
            for conv in history:
                timestamp = conv.get("timestamp", "")
                if conv["role"] == "user":
                    role = "👤 用户"
                elif conv["role"] == "assistant":
                    role = "🤖 助手"
                elif conv["role"] == "tool":
                    role = f"🔧 工具[{conv.get('name', 'unknown')}]"
                else:
                    role = conv["role"]

                content = conv["content"][:100] + "..." if len(conv["content"]) > 100 else conv["content"]
                print(f"\n[{timestamp[:19]}] {role}:")
                print(content)

                # 如果是助手消息且有工具调用，显示工具信息
                if conv["role"] == "assistant" and "tool_calls" in conv and conv["tool_calls"]:
                    tools = [tc["function"]["name"] for tc in conv["tool_calls"]]
                    print(f"  🔗 调用工具: {', '.join(tools)}")
            print("="*50)

    async def show_files(self, args: str = ""):
            """显示项目文件"""
            if self.context_manager._is_host_mode_without_safety():
                print("\n" + tr("commands.file_tree_unavailable_host"))
                return
            structure = self.context_manager.get_project_structure()
            print(f"\n📁 项目文件结构:")
            print(self.context_manager._build_file_tree(structure))
            print(f"\n总计: {structure['total_files']} 个文件, {structure['total_size'] / 1024 / 1024:.2f} MB")

    def set_run_mode(self, mode: str) -> str:
            """统一设置运行模式（fast / thinking；旧值 deep 映射为 thinking）"""
            normalized = mode.lower()
            if normalized == "deep":  # 旧版“深度思考”标识符，统一映射为思考模式
                normalized = "thinking"
            allowed = ["fast", "thinking"]
            if normalized not in allowed:
                raise ValueError(tr("commands_raise.unsupported_mode", mode=mode))
            # 仅思考模型限制
            if getattr(self, "model_profile", {}).get("thinking_only") and normalized != "thinking":
                raise ValueError(tr("commands_raise.thinking_only_model"))
            # fast-only 模型限制
            if getattr(self, "model_profile", {}).get("fast_only") and normalized != "fast":
                raise ValueError(tr("commands_raise.fast_only_model"))
            self.run_mode = normalized
            self.thinking_mode = normalized != "fast"
            self.api_client.thinking_mode = self.thinking_mode
            return self.run_mode

    def set_reasoning_effort(self, effort: Optional[str]) -> Optional[str]:
            """设置会话级推理强度；None 表示默认（请求中不传该参数）。"""
            if effort is None or str(effort).strip() == "":
                self.reasoning_effort = None
            else:
                normalized = str(effort).strip().lower()
                if normalized not in REASONING_EFFORT_LEVELS:
                    raise ValueError(tr("commands_raise.unsupported_reasoning_effort", effort=effort))
                self.reasoning_effort = normalized
            self.api_client.reasoning_effort = self.reasoning_effort
            return self.reasoning_effort

    def apply_model_profile(self, profile: dict):
            """将模型配置应用到 API 客户端"""
            if not profile:
                return
            self.api_client.apply_profile(profile)

    def set_model(self, model_key: str) -> str:
            model_key = resolve_model_key(model_key)
            profile = get_model_profile(model_key)
            if getattr(self.context_manager, "has_images", False) and not model_supports_image(model_key):
                raise ValueError(tr("commands_raise.model_no_image"))
            if getattr(self.context_manager, "has_videos", False) and not model_supports_video(model_key):
                raise ValueError(tr("commands_raise.model_no_video"))
            previous_mode = self.run_mode
            self.model_key = model_key
            self.model_profile = profile
            # 将模型标识传递给底层 API 客户端，便于按模型做兼容处理
            self.api_client.model_key = model_key
            # 应用模型配置
            self.apply_model_profile(profile)
            # fast-only 模型强制快速模式
            if profile.get("fast_only") and self.run_mode != "fast":
                # 记住被强制前的模式，切回支持思考的模型时可恢复
                if previous_mode == "thinking":
                    self._mode_before_fast_only = previous_mode
                self.set_run_mode("fast")
            # 仅思考模型强制思考模式
            if profile.get("thinking_only") and self.run_mode != "thinking":
                self.set_run_mode("thinking")
            # 从 fast-only 模型切回支持思考的模型时，恢复先前模式（若可用）
            if (
                not profile.get("fast_only")
                and self.run_mode == "fast"
                and profile.get("supports_thinking")
            ):
                restore_mode = getattr(self, "_mode_before_fast_only", None)
                if restore_mode == "thinking":
                    try:
                        self.set_run_mode(restore_mode)
                    except ValueError:
                        pass
            if not profile.get("fast_only"):
                self._mode_before_fast_only = None
            try:
                logger.debug(
                    "[ModelSwitch] model=%s run_mode=%s thinking=%s fast_only=%s thinking_only=%s",
                    self.model_key,
                    self.run_mode,
                    self.thinking_mode,
                    bool(profile.get("fast_only")),
                    bool(profile.get("thinking_only")),
                )
            except Exception:
                pass
            # 如果模型支持思考，则保持当前 run_mode；否则无需调整
            return self.model_key

    def get_run_mode_label(self) -> str:
            labels = {
                "fast": "快速模式（无思考）",
                "thinking": "思考模式（使用思考模型）"
            }
            return labels.get(self.run_mode, "快速模式（无思考）")

    async def toggle_mode(self, args: str = ""):
            """切换运行模式"""
            modes = ["fast", "thinking", "deep"]
            target_mode = ""
            if args:
                candidate = args.strip().lower()
                if candidate not in modes:
                    print(f"{OUTPUT_FORMATS['error']} {tr('commands.invalid_mode', mode=args)}")
                    return
                target_mode = candidate
            else:
                current_index = modes.index(self.run_mode) if self.run_mode in modes else 0
                target_mode = modes[(current_index + 1) % len(modes)]
            if target_mode == self.run_mode:
                print(f"{OUTPUT_FORMATS['info']} {tr('commands.mode_already', label=self.get_run_mode_label())}")
                return
            try:
                self.set_run_mode(target_mode)
                print(f"{OUTPUT_FORMATS['info']} {tr('commands.mode_switched', label=self.get_run_mode_label())}")
            except ValueError as exc:
                print(f"{OUTPUT_FORMATS['error']} {exc}")
