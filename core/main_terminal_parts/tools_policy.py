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
    infer_private_skills_dir,
)
from modules.custom_tool_registry import CustomToolRegistry, build_default_tool_category
from modules.custom_tool_executor import CustomToolExecutor
from modules.mcp_server_registry import build_default_mcp_category

try:
    from config.limits import REASONING_EFFORT_LEVELS
except ImportError:
    REASONING_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

from modules.container_monitor import collect_stats, inspect_state
from core.tool_config import TOOL_CATEGORIES
from utils.api_client import APIClient
from utils.context_manager import ContextManager
from utils.tool_result_formatter import format_tool_result_for_context
from utils.logger import setup_logger
from config.model_profiles import (
    get_model_profile,
    get_model_prompt_replacements,
    get_model_context_window,
)

from modules.i18n import tr

logger = setup_logger(__name__)
DISABLE_LENGTH_CHECK = True
PERMISSION_MODES = {"readonly", "approval", "auto_approval", "unrestricted"}
# 运行模式（交互方式档）：计划 / 询问 / 执行。
# 与权限模式（能力档）正交；plan 档会联动锁死权限为只读。
WORK_MODES = {"plan", "ask", "execute"}
WORK_MODE_DEFAULT = "plan"

class MainTerminalToolsPolicyMixin:
    def _ensure_runtime_tool_overrides(self) -> Dict[str, bool]:
                overrides = getattr(self, "runtime_tool_category_overrides", None)
                if not isinstance(overrides, dict):
                    overrides = {}
                    self.runtime_tool_category_overrides = overrides
                return overrides

    def _prune_runtime_tool_overrides(self) -> None:
                overrides = self._ensure_runtime_tool_overrides()
                valid_keys = set(getattr(self, "tool_categories_map", {}).keys())
                for key in list(overrides.keys()):
                    if key not in valid_keys:
                        overrides.pop(key, None)

    def _apply_runtime_tool_overrides(self) -> None:
                overrides = self._ensure_runtime_tool_overrides()
                if not overrides:
                    return
                categories = getattr(self, "tool_categories_map", {}) or {}
                for key, value in list(overrides.items()):
                    if key not in categories:
                        overrides.pop(key, None)
                        continue
                    forced = getattr(self, "admin_forced_category_states", {}).get(key)
                    if isinstance(forced, bool):
                        continue
                    self.tool_category_states[key] = bool(value)

    def apply_personalization_preferences(self, config: Optional[Dict[str, Any]] = None, *, apply_default_model: bool = True, apply_default_modes: bool = True):
                """Apply persisted personalization settings that affect runtime behavior.

                apply_default_modes=False 时跳过 default_model / default_run_mode /
                default_reasoning_effort 三项默认值应用——对话加载链路必须传 False，
                三者以对话 meta 为权威，默认值仅用于创建空对话。
                """
                try:
                    effective_config = config or load_personalization_config(self.data_dir)
                except Exception:
                    effective_config = {}

                # 工具意图开关
                self.tool_intent_enabled = bool(effective_config.get("tool_intent_enabled"))
                # Skill 强约束开关
                self.skill_strict_terminal_enabled = bool(effective_config.get("skill_strict_terminal_enabled", False))
                self.skill_strict_sub_agent_enabled = bool(effective_config.get("skill_strict_sub_agent_enabled", False))
                self.skill_strict_run_command_foreground_enabled = bool(
                    effective_config.get("skill_strict_run_command_foreground_enabled", False)
                )
                self.skill_strict_run_command_background_enabled = bool(
                    effective_config.get("skill_strict_run_command_background_enabled", False)
                )

                # 解析当前启用的 skills（用于强约束“仅对已启用 skill 生效”）
                try:
                    skills_catalog = get_skills_catalog(private_dir=infer_private_skills_dir(self.data_dir))
                    enabled_skills = merge_enabled_skills(
                        effective_config.get("enabled_skills") if isinstance(effective_config, dict) else None,
                        skills_catalog,
                        effective_config.get("skills_catalog_snapshot") if isinstance(effective_config, dict) else None,
                    )
                    self.enabled_skill_ids = set(enabled_skills or [])
                except Exception:
                    self.enabled_skill_ids = set()

                disabled_categories = []
                raw_disabled = effective_config.get("disabled_tool_categories")
                if isinstance(raw_disabled, list):
                    disabled_categories = [
                        key for key in raw_disabled
                        if isinstance(key, str) and key in self.tool_categories_map
                    ]
                self.default_disabled_tool_categories = disabled_categories

                # 图片压缩模式传递给上下文
                img_mode = effective_config.get("image_compression")
                if isinstance(img_mode, str):
                    self.context_manager.image_compression_mode = img_mode

                # Reset category states to defaults before applying overrides
                for key, category in self.tool_categories_map.items():
                    self.tool_category_states[key] = False if key in disabled_categories else category.default_enabled
                self._prune_runtime_tool_overrides()
                self._apply_runtime_tool_overrides()
                self._refresh_disabled_tools()

                # 默认模型偏好（优先应用，再处理运行模式）
                preferred_model = effective_config.get("default_model")
                if apply_default_modes and apply_default_model and isinstance(preferred_model, str) and preferred_model != self.model_key:
                    try:
                        self.set_model(preferred_model)
                    except Exception as exc:
                        logger.warning("忽略无效默认模型: %s (%s)", preferred_model, exc)

                preferred_mode = effective_config.get("default_run_mode")
                if apply_default_modes and isinstance(preferred_mode, str):
                    normalized_mode = preferred_mode.strip().lower()
                    if normalized_mode == "deep":  # 旧版标识符映射
                        normalized_mode = "thinking"
                    if normalized_mode in {"fast", "thinking"} and normalized_mode != self.run_mode:
                        try:
                            self.set_run_mode(normalized_mode)
                        except ValueError:
                            logger.warning("忽略无效默认运行模式: %s", preferred_mode)

                # 默认推理强度（None=默认，不传参）
                if apply_default_modes:
                    preferred_effort = effective_config.get("default_reasoning_effort")
                    if isinstance(preferred_effort, str):
                        preferred_effort = preferred_effort.strip().lower() or None
                        if preferred_effort not in REASONING_EFFORT_LEVELS:
                            preferred_effort = None
                    else:
                        preferred_effort = None
                    try:
                        self.set_reasoning_effort(preferred_effort)
                    except (ValueError, AttributeError):
                        pass

                # 静默禁用工具提示
                self.silent_tool_disable = bool(effective_config.get("silent_tool_disable"))
                permission_mode = effective_config.get("default_permission_mode")
                if isinstance(permission_mode, str) and permission_mode in PERMISSION_MODES:
                    self.default_permission_mode = permission_mode
                else:
                    self.default_permission_mode = "unrestricted"
                if not getattr(self, "current_permission_mode", None):
                    self.current_permission_mode = self.default_permission_mode
                work_mode = effective_config.get("default_work_mode")
                if isinstance(work_mode, str) and work_mode.strip().lower() in WORK_MODES:
                    self.default_work_mode = work_mode.strip().lower()
                else:
                    self.default_work_mode = WORK_MODE_DEFAULT
                if not getattr(self, "current_work_mode", None):
                    self.current_work_mode = self.default_work_mode

    def get_permission_mode(self) -> str:
                mode = str(getattr(self, "current_permission_mode", "unrestricted") or "unrestricted")
                if mode not in PERMISSION_MODES:
                    return "unrestricted"
                return mode

    def get_work_mode(self) -> str:
                mode = str(getattr(self, "current_work_mode", None) or WORK_MODE_DEFAULT).strip().lower()
                if mode not in WORK_MODES:
                    return WORK_MODE_DEFAULT
                return mode

    def set_work_mode(self, mode: str, *, persist: bool = True, conversation_id: Optional[str] = None) -> str:
                normalized = str(mode or "").strip().lower()
                if normalized not in WORK_MODES:
                    raise ValueError(tr("tools_policy.invalid_work_mode"))
                self.current_work_mode = normalized
                if not persist:
                    return normalized

                conv_id = conversation_id or getattr(getattr(self, "context_manager", None), "current_conversation_id", None)
                if conv_id and getattr(self, "context_manager", None):
                    try:
                        self.context_manager._get_conversation_manager_for_id(conv_id).update_conversation_metadata(conv_id,
                            {"work_mode": normalized},
                        )
                        if self.context_manager.current_conversation_id == conv_id:
                            self.context_manager.conversation_metadata["work_mode"] = normalized
                    except Exception:
                        pass
                return normalized

    def switch_work_mode(self, mode: str, *, persist: bool = True, conversation_id: Optional[str] = None) -> Dict[str, Any]:
                """切换运行模式（plan/ask/execute），并处理 plan ⇄ 只读权限的联动。

                - 进入 plan：先把当前权限模式存为 pre_plan_permission_mode，再强制只读；
                - 离开 plan：恢复 pre_plan_permission_mode（无记录则不动当前权限）；
                - ask / execute 之间切换不动权限。
                返回 {"mode": ..., "permission_mode": ...} 供调用方做通知与状态广播。
                """
                normalized = str(mode or "").strip().lower()
                if normalized not in WORK_MODES:
                    raise ValueError(tr("tools_policy.invalid_work_mode"))
                previous = self.get_work_mode()
                conv_id = conversation_id or getattr(getattr(self, "context_manager", None), "current_conversation_id", None)

                entering_plan = normalized == "plan" and previous != "plan"
                leaving_plan = previous == "plan" and normalized != "plan"

                if entering_plan:
                    current_permission = self.get_permission_mode()
                    if current_permission != "readonly":
                        # 记录进入前的权限模式，供离开 plan 时恢复（Claude Code prePlanMode 式）
                        if persist:
                            try:
                                self._persist_runtime_mode_metadata({"pre_plan_permission_mode": current_permission})
                            except Exception:
                                pass
                        self.set_permission_mode("readonly", persist=persist, conversation_id=conv_id)
                    # 执行环境联动：direct ⇒ sandbox。只读权限在宿主机依赖 OS 沙箱硬限制，
                    # direct（完全访问）下无沙箱，只读形同虚设，必须一并锁回沙箱。
                    try:
                        if hasattr(self, "get_execution_mode") and self.get_execution_mode() == "direct":
                            if persist:
                                try:
                                    self._persist_runtime_mode_metadata({"pre_plan_execution_mode": "direct"})
                                except Exception:
                                    pass
                            if hasattr(self, "set_execution_mode"):
                                self.set_execution_mode("sandbox")
                                if persist and hasattr(self, "_persist_runtime_mode_metadata"):
                                    try:
                                        self._persist_runtime_mode_metadata({"execution_mode": "sandbox"})
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                # 先落新模式，再恢复权限：set_permission_mode 的 plan 锁以新模式为准
                self.set_work_mode(normalized, persist=persist, conversation_id=conv_id)
                if leaving_plan:
                    restore = None
                    try:
                        meta = getattr(getattr(self, "context_manager", None), "conversation_metadata", None) or {}
                        restore = str(meta.get("pre_plan_permission_mode") or "").strip().lower() or None
                    except Exception:
                        restore = None
                    if restore not in PERMISSION_MODES:
                        restore = None
                    if not restore:
                        # 无记录（如新对话直接以 plan 创建）：回落到个性化默认权限模式
                        try:
                            prefs = load_personalization_config(getattr(self, "data_dir", None))
                            candidate = str(prefs.get("default_permission_mode") or "").strip().lower()
                            if candidate in PERMISSION_MODES:
                                restore = candidate
                        except Exception:
                            restore = None
                    if restore and restore != "readonly":
                        self.set_permission_mode(restore, persist=persist, conversation_id=conv_id)
                    if persist:
                        try:
                            self._persist_runtime_mode_metadata({"pre_plan_permission_mode": None})
                        except Exception:
                            pass
                    # 恢复执行环境（仅当有明确进入前记录；无记录保持 sandbox，安全默认）
                    try:
                        meta = getattr(getattr(self, "context_manager", None), "conversation_metadata", None) or {}
                        pre_exec = str(meta.get("pre_plan_execution_mode") or "").strip().lower()
                        if pre_exec == "direct" and hasattr(self, "set_execution_mode"):
                            self.set_execution_mode("direct")
                            if persist and hasattr(self, "_persist_runtime_mode_metadata"):
                                try:
                                    self._persist_runtime_mode_metadata({"execution_mode": "direct", "pre_plan_execution_mode": None})
                                except Exception:
                                    pass
                    except Exception:
                        pass
                return {"mode": normalized, "previous_mode": previous, "permission_mode": self.get_permission_mode()}

    def set_permission_mode(self, mode: str, *, persist: bool = True, conversation_id: Optional[str] = None) -> str:
                normalized = str(mode or "").strip().lower()
                if normalized not in PERMISSION_MODES:
                    raise ValueError(tr("tools_policy.invalid_permission_mode"))
                # 计划模式下权限锁死为只读（后端强制）：仅允许保持/切到 readonly。
                # 解除锁定的唯一路径是 switch_work_mode 先切离 plan 再恢复权限。
                if normalized != "readonly" and getattr(self, "get_work_mode", None):
                    try:
                        if self.get_work_mode() == "plan":
                            raise ValueError(tr("tools_policy.plan_mode_locks_readonly"))
                    except AttributeError:
                        pass
                previous = self.get_permission_mode()
                entering_readonly = normalized == "readonly" and previous != "readonly"
                leaving_readonly = previous == "readonly" and normalized != "readonly"
                self.current_permission_mode = normalized
                if not persist:
                    # 只读联动不依赖持久化：运行中 pending 切换路径（apply_pending_runtime_mode_changes）
                    # 也要内存级强制沙箱，只是不落 metadata（无记录则切离时保持沙箱，安全默认）。
                    self._apply_readonly_execution_mode_link(entering_readonly, leaving_readonly, persist=False)
                    return normalized

                conv_id = conversation_id or getattr(getattr(self, "context_manager", None), "current_conversation_id", None)
                if conv_id and getattr(self, "context_manager", None):
                    try:
                        self.context_manager._get_conversation_manager_for_id(conv_id).update_conversation_metadata(conv_id,
                            {"permission_mode": normalized},
                        )
                        if self.context_manager.current_conversation_id == conv_id:
                            self.context_manager.conversation_metadata["permission_mode"] = normalized
                    except Exception:
                        pass
                self._apply_readonly_execution_mode_link(entering_readonly, leaving_readonly, persist=True)
                return normalized

    def _apply_readonly_execution_mode_link(self, entering: bool, leaving: bool, *, persist: bool) -> None:
                """只读权限 ⇄ 执行环境联动：进入 readonly 强制切沙箱，切离恢复进入前执行环境。

                与 plan ⇄ readonly+sandbox 双锁逻辑对称：只读权限在宿主机依赖 OS 沙箱硬限制，
                direct（完全访问）下无沙箱，只读形同虚设，必须一并锁回沙箱。
                进入 readonly 时若执行环境为 direct，先存 pre_readonly_execution_mode 供切离时恢复；
                切离 readonly 时仅当有明确进入前记录才恢复（无记录保持 sandbox，安全默认）。
                """
                if entering:
                    try:
                        if hasattr(self, "get_execution_mode") and self.get_execution_mode() == "direct":
                            if persist and hasattr(self, "_persist_runtime_mode_metadata"):
                                try:
                                    self._persist_runtime_mode_metadata({"pre_readonly_execution_mode": "direct"})
                                except Exception:
                                    pass
                            if hasattr(self, "set_execution_mode"):
                                self.set_execution_mode("sandbox")
                                if persist and hasattr(self, "_persist_runtime_mode_metadata"):
                                    try:
                                        self._persist_runtime_mode_metadata({"execution_mode": "sandbox"})
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                    return
                if leaving:
                    try:
                        meta = getattr(getattr(self, "context_manager", None), "conversation_metadata", None) or {}
                        pre_exec = str(meta.get("pre_readonly_execution_mode") or "").strip().lower()
                    except Exception:
                        pre_exec = ""
                    if pre_exec == "direct" and hasattr(self, "set_execution_mode"):
                        try:
                            self.set_execution_mode("direct")
                            if persist and hasattr(self, "_persist_runtime_mode_metadata"):
                                try:
                                    self._persist_runtime_mode_metadata({"execution_mode": "direct", "pre_readonly_execution_mode": None})
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    elif persist and hasattr(self, "_persist_runtime_mode_metadata"):
                        try:
                            self._persist_runtime_mode_metadata({"pre_readonly_execution_mode": None})
                        except Exception:
                            pass

    def set_tool_category_enabled(self, category: str, enabled: bool) -> None:
                """设置工具类别的启用状态 / Toggle tool category enablement."""
                categories = self.tool_categories_map
                if category not in categories:
                    raise ValueError(tr("tools_policy.unknown_tool_category", category=category))
                forced = self.admin_forced_category_states.get(category)
                if isinstance(forced, bool) and forced != enabled:
                    raise ValueError(tr("tools_policy.category_admin_locked"))
                final_enabled = bool(enabled)
                self.tool_category_states[category] = final_enabled
                self._ensure_runtime_tool_overrides()[category] = final_enabled
                self._refresh_disabled_tools()

    def set_admin_policy(
                self,
                categories: Optional[Dict[str, "ToolCategory"]] = None,
                forced_category_states: Optional[Dict[str, Optional[bool]]] = None,
                disabled_models: Optional[List[str]] = None,
            ) -> None:
                """应用管理员策略（工具分类、强制开关、模型禁用）。"""
                if categories:
                    self.tool_categories_map = dict(categories)
                # 保证自定义工具分类存在（仅当功能启用）
                if self.custom_tools_enabled and "custom" not in self.tool_categories_map:
                    self.tool_categories_map["custom"] = type(next(iter(TOOL_CATEGORIES.values())))(
                        label="自定义工具",
                        tools=[],
                        default_enabled=True,
                        silent_when_disabled=False,
                    )
                if getattr(self, "mcp_tools_enabled", False) and "mcp" not in self.tool_categories_map:
                    default_mcp_cat = build_default_mcp_category()
                    self.tool_categories_map["mcp"] = type(next(iter(TOOL_CATEGORIES.values())))(
                        label=default_mcp_cat["label"],
                        tools=["list_mcp_servers"],
                        default_enabled=True,
                        silent_when_disabled=False,
                    )
                elif "mcp" in self.tool_categories_map:
                    self.tool_categories_map["mcp"].tools = ["list_mcp_servers"]
                # 重新构建启用状态映射，保留已有值
                new_states: Dict[str, bool] = {}
                for key, cat in self.tool_categories_map.items():
                    if key in self.tool_category_states:
                        new_states[key] = self.tool_category_states[key]
                    else:
                        new_states[key] = cat.default_enabled
                self.tool_category_states = new_states
                # 清理已被移除的类别
                for removed in list(self.tool_category_states.keys()):
                    if removed not in self.tool_categories_map:
                        self.tool_category_states.pop(removed, None)

                self.admin_forced_category_states = forced_category_states or {}
                self.admin_disabled_models = disabled_models or []
                self._prune_runtime_tool_overrides()
                self._apply_runtime_tool_overrides()
                self._refresh_disabled_tools()

    def get_tool_settings_snapshot(self) -> List[Dict[str, object]]:
                """获取工具类别状态快照 / Return tool category states snapshot."""
                snapshot: List[Dict[str, object]] = []
                categories = self.tool_categories_map
                for key, category in categories.items():
                    forced = self.admin_forced_category_states.get(key)
                    enabled = self.tool_category_states.get(key, category.default_enabled)
                    if isinstance(forced, bool):
                        enabled = forced
                    snapshot.append({
                        "id": key,
                        "label": category.label,
                        "enabled": enabled,
                        "tools": list(category.tools),
                        "locked": isinstance(forced, bool),
                        "locked_state": forced if isinstance(forced, bool) else None,
                    })
                return snapshot

    def _refresh_disabled_tools(self) -> None:
                """刷新禁用工具列表 / Refresh disabled tool set."""
                disabled: Set[str] = set()
                notice: Set[str] = set()
                categories = self.tool_categories_map
                for key, category in categories.items():
                    state = self.tool_category_states.get(key, category.default_enabled)
                    forced = self.admin_forced_category_states.get(key)
                    if isinstance(forced, bool):
                        state = forced
                    if not state:
                        disabled.update(category.tools)
                        if not getattr(category, "silent_when_disabled", False):
                            notice.update(category.tools)
                self.disabled_tools = disabled
                self.disabled_notice_tools = notice

    def _format_disabled_tool_notice(self) -> Optional[str]:
                """生成禁用工具提示信息 / Format disabled tool notice."""
                if getattr(self, "silent_tool_disable", False):
                    return None
                if not self.disabled_notice_tools:
                    return None

                lines = ["=== 工具可用性提醒 ==="]
                for tool_name in sorted(self.disabled_notice_tools):
                    lines.append(f"{tool_name}：已被用户禁用")
                lines.append("=== 提示结束 ===")
                return "\n".join(lines)
