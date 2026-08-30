# core/main_terminal.py - 主终端（集成对话持久化）

import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

try:
    from config import (
        OUTPUT_FORMATS,
        DATA_DIR,
        MAX_TERMINALS, TERMINAL_BUFFER_SIZE, TERMINAL_DISPLAY_SIZE,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_MODE,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        CUSTOM_TOOLS_ENABLED,
        MCP_TOOLS_ENABLED,
        HOST_EXECUTION_MODE_DEFAULT,
        HOST_SANDBOX_NETWORK_PERMISSION,
    )
except ImportError:
    import sys
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS,
        DATA_DIR,
        MAX_TERMINALS, TERMINAL_BUFFER_SIZE, TERMINAL_DISPLAY_SIZE,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_MODE,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        CUSTOM_TOOLS_ENABLED,
        MCP_TOOLS_ENABLED,
        HOST_EXECUTION_MODE_DEFAULT,
        HOST_SANDBOX_NETWORK_PERMISSION,
    )

from modules.file_manager import FileManager
from modules.search_engine import SearchEngine
from modules.terminal_ops import TerminalOperator
from modules.memory_manager import MemoryManager
from modules.terminal_manager import TerminalManager
from modules.todo_manager import TodoManager
from modules.sub_agent import SubAgentManager
from modules.background_command_manager import BackgroundCommandManager
from modules.ocr_client import OCRClient
from modules.easter_egg_manager import EasterEggManager
from modules.custom_tool_registry import CustomToolRegistry, build_default_tool_category
from modules.custom_tool_executor import CustomToolExecutor
from modules.mcp_server_registry import MCPServerRegistry, build_default_mcp_category
from modules.mcp_client_manager import MCPClientManager


from core.tool_config import TOOL_CATEGORIES
from utils.api_client import APIClient
from utils.context_manager import ContextManager
from utils.conversation_manager import ConversationManager
from utils.host_workspace_debug import write_host_workspace_debug
from config.model_profiles import get_default_model_key, get_model_profile

from core.main_terminal_parts import (
    MainTerminalCommandMixin,
    MainTerminalContextMixin,
    MainTerminalToolsMixin,
)

from modules.i18n import tr

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle


class MainTerminal(MainTerminalCommandMixin, MainTerminalContextMixin, MainTerminalToolsMixin):
    def __init__(
            self,
            project_path: str,
            thinking_mode: bool = False,
            run_mode: Optional[str] = None,
            data_dir: Optional[str] = None,
            container_session: Optional["ContainerHandle"] = None,
            usage_tracker: Optional[object] = None,
        ):
            self.project_path = project_path
            allowed_modes = {"fast", "thinking"}
            if run_mode == "deep":  # 旧版标识符映射
                run_mode = "thinking"
            initial_mode = run_mode if run_mode in allowed_modes else ("thinking" if thinking_mode else "fast")
            self.run_mode = initial_mode
            self.thinking_mode = initial_mode != "fast"  # False=快速模式, True=思考模式
            # 记录因 fast-only 模型被强制切到 fast 前的模式，便于切回可思考模型时恢复
            self._mode_before_fast_only: Optional[str] = None
            # 会话级推理强度（None=默认，不传参）
            self.reasoning_effort: Optional[str] = None
            self.data_dir = Path(data_dir).expanduser().resolve() if data_dir else Path(DATA_DIR).resolve()
            self.usage_tracker = usage_tracker

            # 初始化组件
            self.api_client = APIClient(thinking_mode=self.thinking_mode)
            self.api_client.project_path = project_path
            self.model_key = get_default_model_key()
            self.model_profile = get_model_profile(self.model_key)
            self.apply_model_profile(self.model_profile)
            self.context_manager = ContextManager(project_path, data_dir=str(self.data_dir))
            self.context_manager.main_terminal = self
            self.container_mount_path = TERMINAL_SANDBOX_MOUNT_PATH or "/workspace"
            self.container_cpu_limit = TERMINAL_SANDBOX_CPUS or "未限制"
            self.container_memory_limit = TERMINAL_SANDBOX_MEMORY or "未限制"
            self.project_storage_limit = f"{PROJECT_MAX_STORAGE_MB}MB" if PROJECT_MAX_STORAGE_MB else "未限制"
            self.project_storage_limit_bytes = (
                PROJECT_MAX_STORAGE_MB * 1024 * 1024 if PROJECT_MAX_STORAGE_MB else None
            )
            self.container_session: Optional["ContainerHandle"] = None
            self.memory_manager = MemoryManager(data_dir=str(self.data_dir))
            self.file_manager = FileManager(project_path, container_session=container_session, data_dir=str(self.data_dir))
            self.search_engine = SearchEngine()
            self.terminal_ops = TerminalOperator(project_path, container_session=container_session)
            self.ocr_client = OCRClient(project_path, self.file_manager)
            self.pending_image_view = None  # 供 view_image 工具使用，保存一次性图片附加请求
            self.pending_video_view = None  # 供 view_video 工具使用，保存一次性视频附加请求

            # 新增：终端管理器
            self.terminal_manager = TerminalManager(
                project_path=project_path,
                max_terminals=MAX_TERMINALS,
                terminal_buffer_size=TERMINAL_BUFFER_SIZE,
                terminal_display_size=TERMINAL_DISPLAY_SIZE,
                broadcast_callback=None,  # CLI模式不需要广播
                container_session=container_session,
                network_permission_getter=self.get_network_permission,
                terminal_readonly_getter=self.terminal_readonly_enabled,
            )
            # 让 run_command 复用终端容器，保持环境一致
            self.terminal_ops.attach_terminal_manager(self.terminal_manager)
            self._apply_container_session(container_session)

            self.todo_manager = TodoManager(self.context_manager)
            self.sub_agent_manager = SubAgentManager(
                project_path=self.project_path,
                data_dir=str(self.data_dir),
                container_session=container_session,
                # 对话级隔离（仅 WebTerminal 设置该属性；CLI MainTerminal 无此属性，getattr 回退 None）
                owner_conversation_id=getattr(self, "_bound_conversation_id", None),
            )
            self.sub_agent_manager.set_terminal(self)
            self.background_command_manager = BackgroundCommandManager(
                project_path=self.project_path,
            )
            self.easter_egg_manager = EasterEggManager()
            self._announced_sub_agent_tasks = set()
            self.silent_tool_disable = True  # 是否静默工具禁用提示（默认开启）
            self.current_session_id = 0  # 用于标识不同的任务会话
            # 工具类别（可被管理员动态覆盖）
            self.tool_categories_map = dict(TOOL_CATEGORIES)
            # 自定义工具仅管理员可见/可用
            self.user_role: str = "user"
            self.custom_tools_enabled = bool(CUSTOM_TOOLS_ENABLED)
            self.custom_tool_registry = CustomToolRegistry()
            self.custom_tool_executor = CustomToolExecutor(self.custom_tool_registry, self.terminal_ops)
            if self.custom_tools_enabled:
                default_custom_cat = build_default_tool_category()
                # 若未存在 custom 分类则添加，方便前端/策略统一展示
                if "custom" not in self.tool_categories_map:
                    self.tool_categories_map["custom"] = type(next(iter(TOOL_CATEGORIES.values())))(
                        label=default_custom_cat["label"],
                        tools=default_custom_cat["tools"],
                        default_enabled=True,
                        silent_when_disabled=False,
                    )
            self.mcp_tools_enabled = bool(MCP_TOOLS_ENABLED)
            self.mcp_server_registry = MCPServerRegistry()
            self.mcp_client_manager = MCPClientManager(
                self.mcp_server_registry,
                container_session=container_session,
            )
            self.mcp_tool_alias_map: Dict[str, object] = {}
            if self.mcp_tools_enabled:
                default_mcp_cat = build_default_mcp_category()
                if "mcp" not in self.tool_categories_map:
                    self.tool_categories_map["mcp"] = type(next(iter(TOOL_CATEGORIES.values())))(
                        label=default_mcp_cat["label"],
                        tools=default_mcp_cat["tools"],
                        default_enabled=True,
                        silent_when_disabled=False,
                    )
            self.admin_forced_category_states: Dict[str, Optional[bool]] = {}
            self.admin_disabled_models: List[str] = []
            self.admin_policy_ui_blocks: Dict[str, bool] = {}
            self.admin_policy_version: Optional[str] = None

            # 工具启用状态
            self.tool_category_states: Dict[str, bool] = {
                key: category.default_enabled
                for key, category in self.tool_categories_map.items()
            }
            self.disabled_tools: Set[str] = set()
            self.disabled_notice_tools: Set[str] = set()
            self._refresh_disabled_tools()
            self.default_disabled_tool_categories: List[str] = []
            # 个性化工具意图开关
            self.tool_intent_enabled: bool = False
            # Skill 强约束开关（按个人空间配置）
            self.skill_strict_terminal_enabled: bool = False
            self.skill_strict_sub_agent_enabled: bool = False
            self.skill_strict_run_command_foreground_enabled: bool = False
            self.skill_strict_run_command_background_enabled: bool = False
            self.default_permission_mode: str = "unrestricted"
            # None = 未初始化：由随后的 apply_personalization_preferences 应用个性化默认
            # 权限模式（与 current_work_mode 同一机制，构造时生效一次）；所有读取路径
            # 均按 None→unrestricted 兜底（get_permission_mode / tools_execution）
            self.current_permission_mode: Optional[str] = None
            self.host_execution_mode: str = "sandbox"
            self.host_network_permission: str = "restricted"
            self.pending_permission_mode: Optional[str] = None
            self.pending_execution_mode: Optional[str] = None
            self.pending_network_permission: Optional[str] = None
            # 当前生效的启用 skills（用于强约束判定）
            self.enabled_skill_ids: Set[str] = set()
            self.apply_personalization_preferences()
            self._init_host_execution_mode()
            self._init_host_network_permission()

            # 新增：自动开始新对话
            self._ensure_conversation()

            # 命令映射
            self.commands = {
                "help": self.show_help,
                "exit": self.exit_system,
                "status": self.show_status,
                "memory": self.manage_memory,
                "clear": self.clear_conversation,
                "history": self.show_history,
                "files": self.show_files,
                "mode": self.toggle_mode,
                "terminals": self.show_terminals,
                # 新增：对话管理命令
                "conversations": self.show_conversations,
                "load": self.load_conversation_command,
                "new": self.new_conversation_command,
                "save": self.save_conversation_command
            }

    def _apply_container_session(self, session: Optional["ContainerHandle"]):
            self.container_session = session
            if session and session.mode == "docker":
                self.container_mount_path = session.mount_path or (TERMINAL_SANDBOX_MOUNT_PATH or "/workspace")
            else:
                self.container_mount_path = TERMINAL_SANDBOX_MOUNT_PATH or "/workspace"

    def _init_host_execution_mode(self):
            default_mode = str(HOST_EXECUTION_MODE_DEFAULT or "sandbox").strip().lower()
            self.host_execution_mode = "direct" if default_mode == "direct" else "sandbox"
            self._apply_execution_mode_to_runtime()

    def _apply_execution_mode_to_runtime(self):
            mode = self.get_execution_mode()
            if getattr(self, "terminal_ops", None):
                self.terminal_ops.set_host_execution_mode(mode)
            if getattr(self, "terminal_manager", None):
                self.terminal_manager.set_host_execution_mode(mode)
            if getattr(self, "sub_agent_manager", None):
                self.sub_agent_manager.set_host_execution_mode(mode)
            if getattr(self, "file_manager", None):
                self.file_manager.set_host_execution_mode(mode)

    def _init_host_network_permission(self):
            default_permission = str(HOST_SANDBOX_NETWORK_PERMISSION or "restricted").strip().lower()
            if default_permission not in {"restricted", "full", "none"}:
                default_permission = "restricted"
            self.host_network_permission = default_permission
            # 注意：不再写入 os.environ——网络权限的唯一真相是本实例属性；
            # 进程级 env 会被多个 terminal 实例互相覆盖，曾导致后台指令/
            # 持久终端读到其他实例的权限（不生效或跨工作区串扰）。

    def get_network_permission(self) -> str:
            return getattr(self, "host_network_permission", "restricted")

    def set_network_permission(self, mode: str) -> str:
            normalized = str(mode or "").strip().lower()
            if normalized not in {"restricted", "full", "none"}:
                raise ValueError(tr("main_terminal.invalid_network_permission"))
            self.host_network_permission = normalized
            return normalized

    def get_pending_runtime_modes(self) -> Dict[str, Optional[str]]:
            return {
                "permission_mode": self.pending_permission_mode,
                "execution_mode": self.pending_execution_mode,
                "network_permission": getattr(self, "pending_network_permission", None),
            }

    def queue_permission_mode_change(self, mode: str) -> str:
            normalized = str(mode or "").strip().lower()
            if normalized not in {"readonly", "approval", "auto_approval", "unrestricted"}:
                raise ValueError(tr("main_terminal.invalid_permission_mode"))
            self.pending_permission_mode = normalized
            self._persist_runtime_mode_metadata({
                "pending_permission_mode": normalized,
            })
            return normalized

    def queue_execution_mode_change(self, mode: str) -> str:
            normalized = str(mode or "").strip().lower()
            if normalized not in {"sandbox", "direct"}:
                raise ValueError(tr("main_terminal.invalid_execution_environment"))
            self.pending_execution_mode = normalized
            self._persist_runtime_mode_metadata({
                "pending_execution_mode": normalized,
            })
            return normalized

    def queue_network_permission_change(self, mode: str) -> str:
            normalized = str(mode or "").strip().lower()
            if normalized not in {"restricted", "full", "none"}:
                raise ValueError(tr("main_terminal.invalid_network_permission"))
            self.pending_network_permission = normalized
            self._persist_runtime_mode_metadata({
                "pending_network_permission": normalized,
            })
            return normalized

    def apply_pending_runtime_mode_changes(self) -> List[Dict[str, str]]:
            notices: List[Dict[str, str]] = []
            updates: Dict[str, Any] = {}

            pending_permission = self.pending_permission_mode
            if pending_permission:
                current = self.get_permission_mode() if hasattr(self, "get_permission_mode") else "unrestricted"
                if pending_permission != current:
                    self.set_permission_mode(pending_permission, persist=False)
                    if hasattr(self, "build_runtime_mode_switch_notice"):
                        notice_text = self.build_runtime_mode_switch_notice("permission_mode", pending_permission)
                    else:
                        notice_text = f"权限模式被用户修改为 {pending_permission}"
                    notices.append({
                        "text": notice_text,
                        "source": "permission_change",
                        "kind": "permission_mode",
                        "mode": pending_permission,
                    })
                updates["permission_mode"] = pending_permission
                updates["pending_permission_mode"] = None
                self.pending_permission_mode = None

            pending_execution = self.pending_execution_mode
            if pending_execution:
                current_exec = self.get_execution_mode()
                if pending_execution != current_exec:
                    applied = False
                    try:
                        self.set_execution_mode(pending_execution)
                        applied = True
                    except Exception:
                        # 锁定拒绝（plan / 受限权限档下禁止 direct）：丢弃该 pending，保持沙箱
                        applied = False
                    if applied:
                        if hasattr(self, "build_runtime_mode_switch_notice"):
                            notice_text = self.build_runtime_mode_switch_notice("execution_mode", pending_execution)
                        elif hasattr(self, "_build_execution_mode_switch_notice"):
                            notice_text = self._build_execution_mode_switch_notice(pending_execution)
                        else:
                            notice_text = f"执行环境被用户修改为 {pending_execution}"
                        notices.append({
                            "text": notice_text,
                            "source": "execution_change",
                            "kind": "execution_mode",
                            "mode": pending_execution,
                        })
                        updates["execution_mode"] = pending_execution
                updates["pending_execution_mode"] = None
                self.pending_execution_mode = None

            pending_network = getattr(self, "pending_network_permission", None)
            if pending_network:
                current_network = self.get_network_permission()
                if pending_network != current_network:
                    self.set_network_permission(pending_network)
                    if hasattr(self, "build_runtime_mode_switch_notice"):
                        notice_text = self.build_runtime_mode_switch_notice("network_permission", pending_network)
                    else:
                        notice_text = f"网络权限被用户修改为 {pending_network}"
                    notices.append({
                        "text": notice_text,
                        "source": "network_change",
                        "kind": "network_permission",
                        "mode": pending_network,
                    })
                updates["network_permission"] = pending_network
                updates["pending_network_permission"] = None
                self.pending_network_permission = None

            if updates:
                self._persist_runtime_mode_metadata(updates)

            return notices

    def _persist_runtime_mode_metadata(self, updates: Dict[str, Any]) -> None:
            if not isinstance(updates, dict) or not updates:
                return
            cm = getattr(self, "context_manager", None)
            conv_id = getattr(cm, "current_conversation_id", None) if cm else None
            if not cm or not conv_id:
                return
            try:
                target_manager = cm._get_conversation_manager_for_id(conv_id) if hasattr(cm, "_get_conversation_manager_for_id") else cm.conversation_manager
                target_manager.update_conversation_metadata(conv_id, updates)
                if isinstance(getattr(cm, "conversation_metadata", None), dict):
                    cm.conversation_metadata.update(updates)
            except Exception:
                pass

    def get_execution_mode(self) -> str:
            return "direct" if self.host_execution_mode == "direct" else "sandbox"

    def get_execution_mode_state(self) -> Dict:
            mode = self.get_execution_mode()
            return {
                "mode": mode,
                "default_mode": "direct" if str(HOST_EXECUTION_MODE_DEFAULT).lower() == "direct" else "sandbox",
            }

    def set_execution_mode(self, mode: str) -> Dict:
            normalized = str(mode or "").strip().lower()
            if normalized not in {"sandbox", "direct"}:
                raise ValueError(tr("main_terminal.invalid_execution_environment"))
            # 计划模式下执行环境锁死为沙箱：只读权限依赖 OS 沙箱硬限制，
            # direct 下无沙箱，只读形同虚设。解除锁定的唯一路径是
            # switch_work_mode 先切离 plan 再恢复执行环境。
            if normalized == "direct" and getattr(self, "get_work_mode", None):
                try:
                    if self.get_work_mode() == "plan":
                        raise ValueError(tr("main_terminal.plan_mode_locks_sandbox"))
                except AttributeError:
                    pass
            # 受限权限档（只读/批准/自动审核）执行环境同样锁死为沙箱（与 plan 锁并列）：
            # 切到受限档时由 set_permission_mode 联动强制沙箱，这里拦直接调用（API/队列）
            # 防止受限档期间切回 direct——direct 下无沙箱，只读形同虚设、审批承诺被架空
            # （启发式漏判的写命令无 EPERM 兜底）。仅 unrestricted 可选 direct。
            if normalized == "direct" and getattr(self, "get_permission_mode", None):
                try:
                    if self.get_permission_mode() != "unrestricted":
                        raise ValueError(tr("main_terminal.restricted_mode_locks_sandbox"))
                except AttributeError:
                    pass
            self.host_execution_mode = normalized
            self._apply_execution_mode_to_runtime()
            return self.get_execution_mode_state()

    def _is_host_mode(self) -> bool:
            """判定当前是否运行在宿主机模式，用于豁免配额等限制。"""
            if self.container_session and getattr(self.container_session, "mode", None) != "docker":
                return True
            return (TERMINAL_SANDBOX_MODE or "").lower() == "host"

    def record_model_call(self, is_thinking: bool):
            if self._is_host_mode():
                return True, {}
            tracker = getattr(self, "usage_tracker", None)
            if not tracker:
                return True, {}
            mode = "thinking" if is_thinking else "fast"
            try:
                allowed, info = tracker.check_and_increment(mode)
                if allowed:
                    self._notify_quota_update(mode)
                return allowed, info
            except Exception:
                return True, {}

    def record_search_call(self):
            if self._is_host_mode():
                return True, {}
            tracker = getattr(self, "usage_tracker", None)
            if not tracker:
                return True, {}
            try:
                allowed, info = tracker.check_and_increment("search")
                if allowed:
                    self._notify_quota_update("search")
                return allowed, info
            except Exception:
                return True, {}

    def _notify_quota_update(self, metric: str):
            callback = getattr(self, "quota_update_callback", None)
            if callable(callback):
                try:
                    callback(metric)
                except Exception:
                    pass

    def update_container_session(self, session: Optional["ContainerHandle"]):
            self._apply_container_session(session)
            if getattr(self, "terminal_manager", None):
                self.terminal_manager.update_container_session(session)
            if getattr(self, "terminal_ops", None):
                self.terminal_ops.set_container_session(session)
            if getattr(self, "file_manager", None):
                self.file_manager.set_container_session(session)
            if getattr(self, "mcp_client_manager", None):
                self.mcp_client_manager.set_container_session(session)
            if getattr(self, "sub_agent_manager", None):
                try:
                    self.sub_agent_manager.set_container_session(session)
                except Exception:
                    pass

    def update_project_path(self, project_path: str):
            """更新当前终端绑定的工作目录（宿主机工作区切换场景）。"""
            try:
                resolved_path = str(Path(project_path).expanduser().resolve())
            except Exception:
                resolved_path = str(project_path)

            try:
                current_resolved = str(Path(self.project_path).expanduser().resolve())
            except Exception:
                current_resolved = str(self.project_path)
            try:
                current_context_resolved = str(
                    Path(getattr(getattr(self, "context_manager", None), "project_path", "")).expanduser().resolve()
                )
            except Exception:
                current_context_resolved = str(
                    getattr(getattr(self, "context_manager", None), "project_path", "")
                )
            try:
                current_conversation_scope = str(
                    Path(
                        getattr(
                            getattr(getattr(self, "context_manager", None), "conversation_manager", None),
                            "current_project_path",
                            "",
                        )
                    ).expanduser().resolve()
                )
            except Exception:
                current_conversation_scope = str(
                    getattr(
                        getattr(getattr(self, "context_manager", None), "conversation_manager", None),
                        "current_project_path",
                        "",
                    )
                )

            write_host_workspace_debug(
                "terminal.update_project_path.begin",
                terminal_id=id(self),
                current_project_path=current_resolved,
                target_project_path=resolved_path,
            )

            if (
                resolved_path == current_resolved
                and current_context_resolved == resolved_path
                and current_conversation_scope == resolved_path
            ):
                write_host_workspace_debug(
                    "terminal.update_project_path.skip_same_path",
                    terminal_id=id(self),
                    project_path=resolved_path,
                )
                return

            self.project_path = resolved_path

            # API 客户端
            if getattr(self, "api_client", None):
                try:
                    self.api_client.project_path = resolved_path
                except Exception:
                    pass

            # 上下文管理器（影响 file tree / AGENTS.md 注入 / prompt）
            if getattr(self, "context_manager", None):
                try:
                    new_path = Path(resolved_path).resolve()
                    self.context_manager.project_path = new_path
                    self.context_manager.initial_project_path = new_path
                    self.context_manager.project_snapshot = None
                    self.context_manager._host_runtime_cache = None
                    self.context_manager.conversation_manager = ConversationManager(
                        base_dir=str(getattr(self.context_manager, "data_dir", DATA_DIR)),
                        project_path=str(new_path),
                    )
                    self.context_manager.current_conversation_id = None
                    self.context_manager.conversation_history = []
                    self.context_manager.todo_list = None
                    metadata = getattr(self.context_manager, "conversation_metadata", None)
                    if isinstance(metadata, dict):
                        metadata["project_path"] = str(new_path)
                        try:
                            workspace_root = Path(
                                getattr(self.context_manager.conversation_manager, "workspace_root", "")
                            ).resolve()
                            metadata["project_relative_path"] = new_path.relative_to(workspace_root).as_posix()
                        except Exception:
                            metadata["project_relative_path"] = None
                        # 清理旧工作区文件树快照，避免 prompt 继续展示切换前内容
                        metadata.pop("project_file_tree", None)
                        metadata.pop("project_statistics", None)
                        metadata.pop("project_snapshot_at", None)
                    self._ensure_conversation()
                except Exception:
                    pass

            # 文件/终端操作器
            if getattr(self, "file_manager", None):
                try:
                    self.file_manager.project_path = Path(resolved_path).resolve()
                except Exception:
                    pass
            if getattr(self, "terminal_ops", None):
                try:
                    self.terminal_ops.project_path = Path(resolved_path).resolve()
                except Exception:
                    pass

            # 关闭已有实时终端会话，确保后续 pwd/cwd 以新工作区启动
            if getattr(self, "terminal_manager", None):
                try:
                    self.terminal_manager.project_path = Path(resolved_path).resolve()
                    self.terminal_manager.close_all()
                except Exception:
                    pass

            # 其它依赖 project_path 的模块
            if getattr(self, "ocr_client", None):
                try:
                    self.ocr_client.project_path = Path(resolved_path).resolve()
                except Exception:
                    pass
            if getattr(self, "sub_agent_manager", None):
                try:
                    self.sub_agent_manager.project_path = Path(resolved_path).resolve()
                except Exception:
                    pass
            if getattr(self, "background_command_manager", None):
                try:
                    self.background_command_manager.project_path = Path(resolved_path).resolve()
                except Exception:
                    pass

            # MCP stdio 客户端是长连接子进程，切换工作区后需重建，确保后续 cwd 与路径映射一致。
            if getattr(self, "mcp_client_manager", None):
                try:
                    self.mcp_client_manager.close_all_clients()
                except Exception:
                    pass

            # 强制下次请求重新同步 skills（含 AGENTS.md 场景）
            self._skills_synced_project_path = None

            write_host_workspace_debug(
                "terminal.update_project_path.done",
                terminal_id=id(self),
                project_path=self.project_path,
                context_project_path=str(getattr(getattr(self, "context_manager", None), "project_path", "")),
                current_conversation_id=getattr(getattr(self, "context_manager", None), "current_conversation_id", None),
            )

    def _ensure_conversation(self):
            """确保CLI模式下存在可用的对话ID"""
            if self.context_manager.current_conversation_id:
                return

            latest_list = self.context_manager.get_conversation_list(limit=1, offset=0)
            conversations = latest_list.get("conversations", []) if latest_list else []

            if conversations:
                latest = conversations[0]
                conv_id = latest.get("id")
                if conv_id and self.context_manager.load_conversation_by_id(conv_id):
                    print(f"{OUTPUT_FORMATS['info']} 已加载最近对话: {conv_id}")
                    return

            conversation_id = self.context_manager.start_new_conversation(
                project_path=self.project_path,
                thinking_mode=self.thinking_mode,
                run_mode=self.run_mode,
                metadata_overrides={
                    # 构造时 current_permission_mode 已由 apply_personalization_preferences
                    # 初始化为个性化默认值，这里直接取当前生效值即可
                    "permission_mode": self.get_permission_mode(),
                    "execution_mode": self.get_execution_mode(),
                    "pending_permission_mode": None,
                    "pending_execution_mode": None,
                    # frozen_*_prompt 不在创建时预设，由第一次 build_messages 根据当时的实际模式懒加载并冻结
                },
            )
            print(f"{OUTPUT_FORMATS['info']} 新建对话: {conversation_id}")
