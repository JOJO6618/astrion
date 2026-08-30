import asyncio
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from modules.shallow_versioning import ShallowVersioningManager
from modules.edit_summary import (
    remove_edit_summary_entry,
    rename_edit_summary_entry,
    update_edit_summary,
)

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
        READONLY_RUN_COMMAND_ALLOWED,
        READONLY_RUN_COMMAND_ALLOWED_GIT_SUBCOMMANDS,
        READONLY_RUN_COMMAND_BLOCKED_TOKENS,
        WORKSPACE_SKILLS_DIRNAME,
        WORKSPACE_MEMORY_DIRNAME,
        WORKSPACE_REVIEW_DIRNAME,
    )
    from config.paths import WEB_PRESET_ROLES_DIR
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
        READONLY_RUN_COMMAND_ALLOWED,
        READONLY_RUN_COMMAND_ALLOWED_GIT_SUBCOMMANDS,
        READONLY_RUN_COMMAND_BLOCKED_TOKENS,
        WORKSPACE_SKILLS_DIRNAME,
        WORKSPACE_MEMORY_DIRNAME,
        WORKSPACE_REVIEW_DIRNAME,
    )
    from config.paths import WEB_PRESET_ROLES_DIR

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
    save_personalization_config,
    build_personalization_prompt,
    MAX_SHORT_FIELD_LENGTH,
    MAX_CONSIDERATION_TEXT_LENGTH,
    ALLOWED_THEMES,
    ALLOWED_COMMUNICATION_STYLES,
    ALLOWED_CONVERSATION_CONTINUITY,
)
from modules.skills_manager import (
    get_skills_catalog,
    build_skills_list,
    merge_enabled_skills,
    build_skills_prompt,
    archive_skill_directory,
    sync_workspace_skills,
    infer_private_skills_dir,
)
from modules.custom_tool_registry import CustomToolRegistry, build_default_tool_category
from modules.custom_tool_executor import CustomToolExecutor


from modules.container_monitor import collect_stats, inspect_state
from core.tool_config import TOOL_CATEGORIES
from utils.api_client import APIClient
from utils.context_manager import ContextManager
from utils.tool_result_formatter import format_tool_result_for_context
from utils.logger import setup_logger
from modules.multi_agent.debug_logger import ma_debug
from config.model_profiles import (
    get_model_profile,
    get_model_prompt_replacements,
    get_model_context_window,
)

from modules.i18n import tr

logger = setup_logger(__name__)
DISABLE_LENGTH_CHECK = True

class MainTerminalToolsExecutionMixin:
    _DEFAULT_DOCKER_RISK_MARKERS = {
        "rm", "rmdir", "mv", "cp", "dd", "chmod", "chown", "chgrp",
        "sudo", "su", "passwd", "useradd", "userdel",
        "curl", "wget", "nc", "ncat", "scp", "rsync",
        ".env", ".ssh", "id_rsa", "id_ed25519", ".aws", "token", "secret",
        ">/", ">>", "| sh", "| bash", "| zsh",
    }

    def _is_docker_runtime(self) -> bool:
                session = getattr(self, "container_session", None)
                return bool(session and getattr(session, "mode", None) == "docker")

    def _extract_command_risk_markers(self, command: Any) -> List[str]:
                cmd = str(command or "")
                lowered = cmd.lower()
                markers = self._load_docker_risk_markers()
                hits: List[str] = []
                for marker in markers:
                    if marker in lowered:
                        hits.append(marker)
                # 去重并保持顺序
                dedup: List[str] = []
                seen = set()
                for m in hits:
                    if m in seen:
                        continue
                    seen.add(m)
                    dedup.append(m)
                return dedup

    def _load_docker_risk_markers(self) -> List[str]:
                cache = getattr(self, "_docker_risk_markers_cache", None)
                if isinstance(cache, list) and cache:
                    return cache
                config_path = Path(__file__).resolve().parents[2] / "config" / "docker_risk_markers.json"
                markers: List[str] = []
                try:
                    if config_path.exists():
                        data = json.loads(config_path.read_text(encoding="utf-8"))
                        raw = data.get("risk_markers") if isinstance(data, dict) else None
                        if isinstance(raw, list):
                            for item in raw:
                                text = str(item or "").strip().lower()
                                if text:
                                    markers.append(text)
                except Exception:
                    markers = []
                if not markers:
                    markers = sorted(self._DEFAULT_DOCKER_RISK_MARKERS)
                self._docker_risk_markers_cache = markers
                return markers

    @staticmethod
    def _mcp_disabled_message() -> str:
                return tr("tools_exec.mcp_disabled_docker_mode")

    def _is_mcp_disabled_in_docker_mode(self) -> bool:
                session = getattr(self, "container_session", None)
                return bool(session and getattr(session, "mode", None) == "docker")

    _TERMINAL_SERIES_TOOLS = {
        "terminal_session",
        "terminal_input",
        "terminal_snapshot",
    }
    _SUB_AGENT_SERIES_TOOLS = {
        "create_sub_agent",
        "close_sub_agent",
        "terminate_sub_agent",
        "get_sub_agent_status",
    }
    _READONLY_ALLOWED_TOOLS = {
        "web_search",
        "extract_webpage",
        "read_file",
        "read_skill",
        "view_image",
        "view_video",
        "vlm_analyze",
        "ocr_image",
        "update_memory",
        "recall_project_memory",
        "search_project_memory",
        "update_project_memory",
        "conversation_search",
        "conversation_review",
        "todo_create",
        "todo_update_task",
        "todo_get",
        "sleep",
        "ask_user",
        # 以下为只读/调研性质或会话状态管理类工具，只读模式（含计划模式）放行：
        # 终端会话管理与快照（terminal_input 仍禁止，防止经持久终端执行写命令）
        "terminal_session",
        "terminal_snapshot",
        # 子智能体全生命周期（只读权限会传播给子智能体，计划模式调研主力）
        "create_sub_agent",
        "close_sub_agent",
        "terminate_sub_agent",
        "get_sub_agent_status",
        # MCP 服务列举（mcp__* 工具在 evaluate_tool_permission 中按前缀放行）
        "list_mcp_servers",
        # 工作流只读查询（激活/推进/停用/保存等状态写操作仍禁止）
        "list_workflows",
        "get_workflow_status",
    }
    _APPROVAL_REQUIRED_TOOLS = {
        "run_command",
        "terminal_input",
        "write_file",
        "edit_file",
        "create_file",
        "create_folder",
        "delete_file",
        "rename_file",
        "save_webpage",
        "terminal_session",
        "create_skill",
        "save_workflow",
    }

    # 只读命令文本判定（启发式）：用于 approval/auto_approval 的「是否需要审批」决策。
    # 2026-08-30 起不再是安全边界——docker 只读由非特权 uid 内核 DAC 强制
    # （modules/docker_readonly_exec.py），宿主机由 OS 沙箱强制。已知可绕过
    # （如 find . -delete），绕过启发式只会让命令多走一次审批，不会造成写入。
    # 扩展的只读命令白名单（包含常用管道命令）
    _READONLY_ALLOWED_EXECUTABLES = {
        "grep", "find", "ls", "pwd", "tree", "cat", "head", "tail",
        "less", "rg", "wc", "du", "stat", "file", "sed", "awk", "git",
        "sort", "uniq", "cut", "tr", "echo", "printf", "date",
        "basename", "dirname", "which", "strings", "ps", "who", "id",
    }

    # 管道中绝对禁止的目标命令（宁可错杀）
    _DANGEROUS_PIPE_TARGETS = {
        "sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh",
        "rm", "mv", "cp", "dd", "rmdir", "unlink", "mkdir", "touch",
        "chmod", "chown", "chgrp", "chattr", "setfacl",
        "tee", "sponge", "xargs", "parallel",
        "exec", "source", ".", "eval",
    }

    def _is_readonly_run_command_allowed(self, command: Any) -> bool:
                cmd = str(command or "").strip()
                if not cmd:
                    return False
                lowered = cmd.lower()

                # 1. 绝对禁止的token（子shell、逻辑运算符、重定向）
                # 管道符 | 单独处理，不在此禁止
                absolute_forbidden = ("&&", "||", ";", ">", "<", "$(", "`", "&",)
                if any(token in cmd for token in absolute_forbidden):
                    return False

                # 2. 如果包含管道，使用管道专用检查
                if "|" in cmd:
                    return self._is_readonly_pipeline_allowed(cmd)

                # 3. 普通单命令检查
                return self._is_readonly_single_command_allowed(cmd)

    def _is_readonly_pipeline_allowed(self, command: str) -> bool:
                """检查管道链中每个命令是否都是只读的"""
                # 分割管道段（保留空字符串检查）
                segments = [seg.strip() for seg in command.split("|") if seg.strip()]
                if not segments:
                    return False

                for segment in segments:
                    # 段内禁止子shell（双重检查）
                    if "$(" in segment or "`" in segment:
                        return False

                    # 提取命令名
                    parts = segment.split()
                    if not parts:
                        return False
                    executable = parts[0].lower()

                    # 检查段内是否有输出重定向（管道内也不允许）
                    if ">" in segment:
                        return False

                    # 检查是否是安全的只读命令
                    if not self._is_safe_readonly_executable(executable, segment):
                        return False

                    # 检查是否是危险的管道目标
                    if self._is_dangerous_pipe_target(segment):
                        return False

                return True

    def _is_readonly_single_command_allowed(self, command: str) -> bool:
                """检查单个命令是否是只读的"""
                lowered = command.lower()
                parts = command.split()
                if not parts:
                    return False

                executable = parts[0].lower()

                # 使用基础白名单
                basic_allowed = set(READONLY_RUN_COMMAND_ALLOWED or ())
                if executable in basic_allowed and executable not in {"git", "sed", "awk"}:
                    return True

                # 特殊命令检查
                if executable == "git":
                    if len(parts) < 2:
                        return False
                    git_sub = parts[1].lower()
                    return git_sub in set(READONLY_RUN_COMMAND_ALLOWED_GIT_SUBCOMMANDS or ())

                if executable == "sed":
                    return len(parts) >= 2 and parts[1] == "-n"

                if executable == "awk":
                    return "system(" not in lowered

                return False

    def _is_safe_readonly_executable(self, executable: str, segment: str) -> bool:
                """检查单个命令是否在扩展白名单中"""
                if executable not in self._READONLY_ALLOWED_EXECUTABLES:
                    return False

                # 特殊命令的安全检查
                lowered = segment.lower()

                if executable == "sed":
                    return "-n" in segment

                if executable == "awk":
                    return "system(" not in lowered

                if executable == "git":
                    parts = segment.split()
                    if len(parts) < 2:
                        return False
                    git_sub = parts[1].lower()
                    return git_sub in set(READONLY_RUN_COMMAND_ALLOWED_GIT_SUBCOMMANDS or ())

                # 检查其他命令是否有危险的参数组合
                # 例如：sort -o（输出到文件）是危险的
                if executable == "sort":
                    return "-o" not in segment and "--output" not in lowered

                return True

    def _is_dangerous_pipe_target(self, segment: str) -> bool:
                """检查管道目标是否会执行写入/修改/危险操作"""
                lowered = segment.lower()
                parts = lowered.split()
                if not parts:
                    return False
                cmd = parts[0]

                # 检查是否在危险命令列表中
                if cmd in self._DANGEROUS_PIPE_TARGETS:
                    return True

                # 检查是否有危险的参数（如 -exec, -delete 等）
                dangerous_args = ("-exec", "-delete", "-ok", "-execdir", "-okdir")
                if any(arg in lowered for arg in dangerous_args):
                    return True

                return False

    def evaluate_tool_permission(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
                mode = "unrestricted"
                try:
                    mode = self.get_permission_mode()
                except Exception:
                    mode = str(getattr(self, "current_permission_mode", "unrestricted") or "unrestricted")
                args = arguments or {}

                if mode == "readonly":
                    if tool_name == "run_command":
                        return {"allowed": True, "mode": mode}
                    # 计划模式例外：计划文档（工作区 .astrion/plan/ 下的 .md）是唯一可写对象
                    if tool_name in {"write_file", "edit_file"} and self._is_plan_workspace_file(args):
                        return {"allowed": True, "mode": mode}
                    # 计划模式下允许提交计划求批准
                    if tool_name == "submit_plan" and self._is_plan_work_mode():
                        return {"allowed": True, "mode": mode}
                    # MCP 工具（mcp__<服务>__<工具>）在只读模式下放行：
                    # 工具名动态生成无法静态区分读写，经用户确认全部允许（副作用风险由提示词约束）
                    if tool_name.startswith("mcp__"):
                        return {"allowed": True, "mode": mode}
                    if tool_name not in self._READONLY_ALLOWED_TOOLS:
                        return {
                            "allowed": False,
                            "mode": mode,
                            "code": "readonly_denied",
                            "message": tr("tools_exec.readonly_denied")
                        }
                    return {"allowed": True, "mode": mode}

                if mode == "approval":
                    if tool_name == "run_command":
                        command_text = args.get("command")
                        readonly_ok = self._is_readonly_run_command_allowed(command_text)
                        risk_markers = self._extract_command_risk_markers(command_text)
                        requires = (not readonly_ok) or bool(risk_markers)
                        return {
                            "allowed": True,
                            "mode": mode,
                            "requires_approval": requires,
                            "risk_markers": risk_markers,
                        }
                    if tool_name == "terminal_input":
                        command_text = args.get("command") or args.get("input")
                        if self._is_docker_runtime():
                            readonly_ok = self._is_readonly_run_command_allowed(command_text)
                            risk_markers = self._extract_command_risk_markers(command_text)
                            requires = (not readonly_ok) or bool(risk_markers)
                            return {
                                "allowed": True,
                                "mode": mode,
                                "requires_approval": requires,
                                "risk_markers": risk_markers,
                            }
                        if self._is_readonly_run_command_allowed(command_text):
                            return {"allowed": True, "mode": mode, "requires_approval": False}
                    if tool_name in self._APPROVAL_REQUIRED_TOOLS:
                        return {"allowed": True, "mode": mode, "requires_approval": True}
                    return {"allowed": True, "mode": mode}

                if mode == "auto_approval":
                    if tool_name in {"write_file", "edit_file"}:
                        if self._is_docker_runtime():
                            return {"allowed": True, "mode": mode, "requires_approval": False}
                        path = args.get("file_path") or args.get("path") or ""
                        try:
                            valid, _, full_path = self.file_manager._validate_path(str(path))
                            if valid and full_path is not None:
                                project_root = Path(self.context_manager.project_path).resolve()
                                resolved = Path(full_path).resolve()
                                if resolved == project_root or project_root in resolved.parents:
                                    return {"allowed": True, "mode": mode, "requires_approval": False}
                        except Exception:
                            pass
                        return {"allowed": True, "mode": mode, "requires_approval": True}
                    if tool_name == "run_command":
                        command_text = args.get("command")
                        readonly_ok = self._is_readonly_run_command_allowed(command_text)
                        risk_markers = self._extract_command_risk_markers(command_text)
                        requires = (not readonly_ok) or bool(risk_markers)
                        return {
                            "allowed": True,
                            "mode": mode,
                            "requires_approval": requires,
                            "risk_markers": risk_markers,
                        }
                    if tool_name == "terminal_input":
                        command_text = args.get("command") or args.get("input")
                        if self._is_docker_runtime():
                            readonly_ok = self._is_readonly_run_command_allowed(command_text)
                            risk_markers = self._extract_command_risk_markers(command_text)
                            requires = (not readonly_ok) or bool(risk_markers)
                            return {
                                "allowed": True,
                                "mode": mode,
                                "requires_approval": requires,
                                "risk_markers": risk_markers,
                            }
                        if self._is_readonly_run_command_allowed(command_text):
                            return {"allowed": True, "mode": mode, "requires_approval": False}
                    if tool_name in self._APPROVAL_REQUIRED_TOOLS:
                        return {"allowed": True, "mode": mode, "requires_approval": True}
                    return {"allowed": True, "mode": mode}

                return {"allowed": True, "mode": mode}

    def _is_plan_work_mode(self) -> bool:
                try:
                    return hasattr(self, "get_work_mode") and self.get_work_mode() == "plan"
                except Exception:
                    return False

    def _is_plan_workspace_file(self, args: Dict[str, Any]) -> bool:
                """计划模式写例外：仅当目标路径是工作区 .astrion/plan/ 下的 .md 文件。"""
                try:
                    if not self._is_plan_work_mode():
                        return False
                    path = (args or {}).get("file_path") or (args or {}).get("path") or ""
                    if not path:
                        return False
                    valid, _, full_path = self.file_manager._validate_path(str(path))
                    if not valid or full_path is None:
                        return False
                    project_root = Path(self.context_manager.project_path).resolve()
                    plan_dir = (project_root / ".astrion" / "plan").resolve()
                    resolved = Path(full_path).resolve()
                    if resolved != plan_dir and plan_dir not in resolved.parents:
                        return False
                    return resolved.suffix.lower() == ".md"
                except Exception:
                    return False

    @staticmethod
    def _skill_meta_key(skill_id: str) -> str:
                return f"skill_read::{skill_id}"

    @staticmethod
    def _file_read_meta_key() -> str:
                return "read_file::visited_paths"

    def _normalize_tool_path(self, path: Any) -> str:
                raw = str(path or "").strip().replace("\\", "/")
                if not raw:
                    return ""
                if raw.startswith("/workspace/"):
                    raw = raw.split("/workspace/", 1)[1]
                try:
                    raw_path = Path(raw)
                    if raw_path.is_absolute():
                        abs_path = raw_path.expanduser().resolve()
                    else:
                        abs_path = (Path(self.context_manager.project_path).resolve() / raw_path).resolve()
                    project_root = Path(self.context_manager.project_path).resolve()
                    try:
                        rel = abs_path.relative_to(project_root)
                        return str(rel).replace("\\", "/").lstrip("./").lower()
                    except Exception:
                        return str(abs_path).replace("\\", "/").lower()
                except Exception:
                    return raw.lstrip("./").lower()

    def _resolve_create_skill_source_dir(self, source_dir: Any) -> Optional[Path]:
                raw = str(source_dir or "").strip()
                if not raw:
                    return None
                project_root = Path(self.context_manager.project_path).expanduser().resolve()
                if raw == "/workspace":
                    return project_root
                if raw.startswith("/workspace/"):
                    return (project_root / raw.split("/workspace/", 1)[1]).resolve()
                candidate = Path(raw).expanduser()
                if candidate.is_absolute():
                    return candidate.resolve()
                return (project_root / candidate).resolve()

    def _get_create_skill_target_root(self) -> Path:
                inferred = infer_private_skills_dir(self.data_dir)
                if inferred:
                    return inferred
                return (Path(self.data_dir).expanduser().resolve().parent / "agentskills").resolve()

    def _handle_create_skill_tool(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
                source = self._resolve_create_skill_source_dir(arguments.get("source_dir"))
                if source is None:
                    return {"success": False, "error": tr("tools_exec.source_dir_empty")}

                target_root = self._get_create_skill_target_root()
                result = archive_skill_directory(source, target_root)

                if result.get("success"):
                    try:
                        config = load_personalization_config(self.data_dir)
                        catalog = get_skills_catalog(private_dir=infer_private_skills_dir(self.data_dir))
                        enabled = merge_enabled_skills(
                            config.get("enabled_skills") if isinstance(config, dict) else None,
                            catalog,
                            config.get("skills_catalog_snapshot") if isinstance(config, dict) else None,
                        )
                        sync_result = sync_workspace_skills(
                            self.project_path,
                            enabled,
                            private_dir=infer_private_skills_dir(self.data_dir),
                        )
                        if not sync_result.get("success"):
                            result["sync_warning"] = tr("tools_exec.skills_sync_failed")
                    except Exception as exc:
                        result["sync_warning"] = tr("tools_exec.skills_sync_failed")

                if result.get("success"):
                    result["summary"] = (
                        tr("tools_exec.skill_archived", skill_name=result.get('skill_name'))
                    )
                    # 成功时不向模型/前端暴露宿主机或 Docker 内部路径。
                    result.pop("source_dir", None)
                    result.pop("target_dir", None)
                return result

    def _handle_activate_workflow_tool(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
                """activate_workflow：加载定义+快照+初始化（幂等规则由编排层处理）。"""
                from server.workflow_flow import activate_workflow
                conversation_id = getattr(self.context_manager, "current_conversation_id", None)
                if not conversation_id:
                    return {"success": False, "error": tr("tools_exec.workflow_no_conversation")}
                try:
                    msg_index = len(getattr(self.context_manager, "conversation_history", []) or [])
                except Exception:
                    msg_index = 0
                result = activate_workflow(
                    data_dir=self.data_dir,
                    conversation_id=str(conversation_id),
                    name=str(arguments.get("name") or ""),
                    msg_index=msg_index,
                )
                if result.get("success"):
                    return {
                        "success": True,
                        "already": bool(result.get("already")),
                        "message": result.get("text"),
                    }
                return {"success": False, "error": result.get("error")}

    def _handle_get_workflow_status_tool(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
                from server.workflow_flow import build_status_text
                conversation_id = getattr(self.context_manager, "current_conversation_id", None)
                if not conversation_id:
                    return {"success": False, "error": tr("tools_exec.no_open_conversation")}
                return {
                    "success": True,
                    "message": build_status_text(data_dir=self.data_dir, conversation_id=str(conversation_id)),
                }

    def _handle_list_workflows_tool(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
                from modules.workflow_manager import list_workflows, read_workflow_markdown

                name = str(arguments.get("name") or "").strip()
                if name:
                    try:
                        text = read_workflow_markdown(name, self.data_dir)
                    except FileNotFoundError as exc:
                        return {"success": False, "error": str(exc)}
                    except ValueError as exc:
                        return {"success": False, "error": str(exc)}
                    return {"success": True, "workflow_name": name, "message": text}

                items = list_workflows(self.data_dir)
                if not items:
                    return {
                        "success": True,
                        "count": 0,
                        "workflows": [],
                        "message": tr("tools_exec.workflow_none"),
                    }
                lines = [tr("tools_exec.workflows_count", count=len(items))]
                for item in items:
                    src = tr("tools_exec.workflow_builtin") if item.get("source") == "builtin" else tr("tools_exec.workflow_user")
                    lines.append(
                        tr("tools_exec.workflow_item_line",
                           name=item.get('name'),
                           description=item.get('description') or tr("tools_exec.workflow_no_description"),
                           src=src,
                           node_count=item.get('nodeCount'))
                    )
                return {
                    "success": True,
                    "count": len(items),
                    "workflows": items,
                    "message": "\n".join(lines),
                }

    def _handle_save_workflow_tool(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
                from modules.workflow_manager import archive_workflow_directory

                source = self._resolve_create_skill_source_dir(arguments.get("source_dir"))
                if source is None:
                    return {"success": False, "error": tr("tools_exec.source_dir_empty")}
                result = archive_workflow_directory(
                    source,
                    self.data_dir,
                    overwrite=bool(arguments.get("overwrite")),
                )
                if result.get("success"):
                    note = tr("tools_exec.workflow_overwritten_note") if result.get("overwritten") else ""
                    if result.get("shadows_builtin"):
                        note = tr("tools_exec.workflow_shadows_builtin_note")
                    result["summary"] = tr(
                        "tools_exec.workflow_archived_summary",
                        workflow_name=result.get('workflow_name'),
                        node_count=result.get('node_count'),
                        note=note,
                    )
                return result

    def _handle_update_project_memory(self, name: str, description: str, content: str) -> Dict[str, Any]:
                """处理 update_project_memory：写入 .astrion/memory/{name}.md"""
                safe_name = str(name).strip()
                if not safe_name or "/" in safe_name or "\\" in safe_name:
                    return {"success": False, "error": tr("tools_exec.memory_name_invalid", name=name)}

                # 确保 .astrion/memory/ 目录存在
                memory_dir = Path(self.project_path) / WORKSPACE_MEMORY_DIRNAME
                try:
                    memory_dir.mkdir(parents=True, exist_ok=True)
                except Exception as exc:
                    return {"success": False, "error": tr("tools_exec.memory_dir_create_failed", error=str(exc))}

                # 拼接完整文件内容（YAML frontmatter + markdown）
                full_content = f"---\nname: {safe_name}\ndescription: {description}\n---\n\n{content}\n"
                file_path = memory_dir / f"{safe_name}.md"

                try:
                    file_path.write_text(full_content, encoding="utf-8")
                except Exception as exc:
                    return {"success": False, "error": tr("tools_exec.memory_file_write_failed", error=str(exc))}

                return {
                    "success": True,
                    "memory_name": safe_name,
                    "path": str(file_path),
                    "summary": tr("tools_exec.memory_updated" if file_path.exists() else "tools_exec.memory_created", safe_name=safe_name),
                }

    def _mark_skill_read_from_result(self, tool_name: str, arguments: Dict[str, Any], result: Dict[str, Any]) -> None:
                if tool_name not in {"read_file", "read_skill"}:
                    return
                if not isinstance(result, dict) or not result.get("success"):
                    return
                normalized = self._normalize_tool_path(result.get("path") or arguments.get("path"))
                if not normalized:
                    return
                if normalized.endswith(f"{WORKSPACE_SKILLS_DIRNAME}/terminal-guide/skill.md"):
                    self.context_manager._set_meta_flag(self._skill_meta_key("terminal-guide"), True)
                if normalized.endswith(f"{WORKSPACE_SKILLS_DIRNAME}/sub-agent-guide/skill.md"):
                    self.context_manager._set_meta_flag(self._skill_meta_key("sub-agent-guide"), True)
                if normalized.endswith(f"{WORKSPACE_SKILLS_DIRNAME}/run-command-guide/skill.md"):
                    self.context_manager._set_meta_flag(self._skill_meta_key("run-command-guide"), True)

    def _get_visited_read_files(self) -> Set[str]:
                raw = self.context_manager._get_meta_flag(self._file_read_meta_key(), [])
                if isinstance(raw, set):
                    items = raw
                elif isinstance(raw, list):
                    items = set(raw)
                elif isinstance(raw, tuple):
                    items = set(raw)
                else:
                    items = set()

                visited: Set[str] = set()
                for item in items:
                    normalized = self._normalize_tool_path(item)
                    if normalized:
                        visited.add(normalized)
                return visited

    def _mark_file_read_from_result(self, tool_name: str, arguments: Dict[str, Any], result: Dict[str, Any]) -> None:
                if tool_name not in {"read_file", "read_skill"}:
                    return
                if not isinstance(result, dict) or not result.get("success"):
                    return
                normalized = self._normalize_tool_path(result.get("path") or arguments.get("path"))
                if not normalized:
                    return
                visited = self._get_visited_read_files()
                if normalized in visited:
                    return
                visited.add(normalized)
                self.context_manager._set_meta_flag(
                    self._file_read_meta_key(),
                    sorted(visited),
                )

    def _has_read_file_in_conversation(self, path: Any) -> bool:
                normalized = self._normalize_tool_path(path)
                if not normalized:
                    return False
                return normalized in self._get_visited_read_files()

    def _mark_file_as_read_visited(self, path: Any) -> None:
                normalized = self._normalize_tool_path(path)
                if not normalized:
                    return
                visited = self._get_visited_read_files()
                if normalized in visited:
                    return
                visited.add(normalized)
                self.context_manager._set_meta_flag(
                    self._file_read_meta_key(),
                    sorted(visited),
                )

    # ---------------- 快捷窗口：本次对话编辑/创建文件记录 ----------------

    def _get_conversation_manager_for_edited_files(self, conversation_id: str):
        cm = getattr(self, "context_manager", None)
        if cm is None:
            return None
        router = getattr(cm, "_get_conversation_manager_for_id", None)
        if callable(router):
            try:
                return router(conversation_id)
            except Exception:
                pass
        return getattr(cm, "conversation_manager", None)

    def _mutate_edited_files(self, mutator) -> None:
        """读取-修改-写回当前对话 metadata.edited_files，并实时广播给前端。"""
        try:
            cm = getattr(self, "context_manager", None)
            if cm is None:
                return
            conv_id = getattr(self, "_bound_conversation_id", None) or getattr(cm, "current_conversation_id", None)
            if not conv_id:
                return
            manager = self._get_conversation_manager_for_edited_files(conv_id)
            if manager is None:
                return
            data = manager.load_conversation(conv_id)
            if not data:
                return
            metadata = data.get("metadata", {}) or {}
            raw = metadata.get("edited_files")
            entries = [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
            changed = mutator(entries)
            if not changed:
                return
            if not manager.update_conversation_metadata(conv_id, {"edited_files": entries}):
                return
            callback = getattr(cm, "_web_terminal_callback", None)
            if callable(callback):
                try:
                    callback("edited_files_updated", {
                        "conversation_id": conv_id,
                        "edited_files": entries,
                    })
                except Exception:
                    pass
        except Exception as exc:
            print(f"⚠️ 更新对话编辑文件记录失败: {exc}")

    def _record_edited_file(self, path: Any, op: str) -> None:
        """记录本次对话中编辑/创建过的文件（相对路径，同 path 去重更新）。"""
        rel_path = str(path or "").strip().replace("\\", "/")
        if not rel_path:
            return

        def _upsert(entries: List[Dict[str, Any]]) -> bool:
            now = datetime.now().isoformat()
            for item in entries:
                if item.get("path") == rel_path:
                    item["op"] = op
                    item["ts"] = now
                    return True
            entries.append({"path": rel_path, "op": op, "ts": now})
            return True

        self._mutate_edited_files(_upsert)

    def _remove_edited_file(self, path: Any) -> None:
        """文件被删除后从记录中移除。"""
        rel_path = str(path or "").strip().replace("\\", "/")
        if not rel_path:
            return

        def _remove(entries: List[Dict[str, Any]]) -> bool:
            before = len(entries)
            entries[:] = [item for item in entries if item.get("path") != rel_path]
            return len(entries) != before

        self._mutate_edited_files(_remove)

    def _rename_edited_file(self, old_path: Any, new_path: Any) -> None:
        """文件重命名后同步更新记录中的路径。"""
        old_rel = str(old_path or "").strip().replace("\\", "/")
        new_rel = str(new_path or "").strip().replace("\\", "/")
        if not old_rel or not new_rel or old_rel == new_rel:
            return

        def _rename(entries: List[Dict[str, Any]]) -> bool:
            for item in entries:
                if item.get("path") == old_rel:
                    item["path"] = new_rel
                    item["ts"] = datetime.now().isoformat()
                    return True
            return False

        self._mutate_edited_files(_rename)

    # ---------------- 本次工作编辑摘要（user 消息 metadata.edit_summary） ----------------

    def _web_callback_for_edit_summary(self):
        cm = getattr(self, "context_manager", None)
        return getattr(cm, "_web_terminal_callback", None) if cm is not None else None

    def _update_edit_summary(self, path: Any, original_text: Any, current_text: Any) -> None:
        """write_file/edit_file 成功后，把该文件合并 diff 写入当前工作 user 消息 metadata。"""
        try:
            if not isinstance(current_text, str):
                # 容器模式 write_file 不返回 new_file：回读文件获取当前内容
                try:
                    read_result = self.file_manager.read_file(str(path))
                    if isinstance(read_result, dict) and read_result.get("success"):
                        current_text = read_result.get("content")
                except Exception:
                    pass
            update_edit_summary(
                self.context_manager,
                path=path,
                original_text=original_text if isinstance(original_text, str) else None,
                current_text=current_text if isinstance(current_text, str) else None,
                web_callback=self._web_callback_for_edit_summary(),
            )
        except Exception as exc:
            print(f"⚠️ 更新编辑摘要失败: {exc}")

    def _remove_edit_summary(self, path: Any) -> None:
        try:
            remove_edit_summary_entry(
                self.context_manager,
                path=path,
                web_callback=self._web_callback_for_edit_summary(),
            )
        except Exception as exc:
            print(f"⚠️ 移除编辑摘要失败: {exc}")

    def _rename_edit_summary(self, old_path: Any, new_path: Any) -> None:
        try:
            rename_edit_summary_entry(
                self.context_manager,
                old_path=old_path,
                new_path=new_path,
                web_callback=self._web_callback_for_edit_summary(),
            )
        except Exception as exc:
            print(f"⚠️ 重命名编辑摘要失败: {exc}")

    def _track_shallow_versioning(self, file_path: Any) -> None:
        """Track a file edit for shallow versioning (conversation-scoped undo)."""
        conversation_id = getattr(self.context_manager, "current_conversation_id", None)
        # 优先使用当前用户消息的 message_id，让文件修改归属到正确的输入节点。
        message_id = getattr(self.context_manager, "current_shallow_message_id", None) or conversation_id
        from utils.perf_log import perf_log
        perf_log("_track_shallow_versioning called", extra={
            "conversation_id": conversation_id,
            "message_id": message_id,
            "project_path": str(self.project_path),
            "data_dir": str(self.data_dir),
            "file_path": str(file_path),
        })
        if not conversation_id or not self.project_path or not self.data_dir:
            perf_log("_track_shallow_versioning skipped", extra={"reason": "missing_required"})
            return
        try:
            manager = ShallowVersioningManager(
                project_path=self.project_path,
                data_dir=self.data_dir,
                conversation_id=conversation_id,
            )
            manager.track_edit(file_path, message_id)
            perf_log("_track_shallow_versioning done", extra={
                "conversation_id": conversation_id,
                "message_id": message_id,
                "file_path": str(file_path),
                "tracked_files_count": len(getattr(manager, "_tracked_files", set())),
            })
        except Exception as exc:
            perf_log("_track_shallow_versioning error", extra={"error": str(exc)})
            # Shallow versioning is best-effort; never block the tool result.
            pass

    def _target_file_exists(self, path: Any) -> bool:
                try:
                    valid, _, full_path = self.file_manager._validate_path(str(path or ""))
                    if not valid or full_path is None:
                        return False
                    return full_path.exists() and full_path.is_file()
                except Exception:
                    return False

    def _check_read_before_edit_prerequisite(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                target_path = arguments.get("file_path")
                if not target_path:
                    return None

                if tool_name == "write_file":
                    append_mode = bool(arguments.get("append", False))
                    if append_mode:
                        return None
                    # 覆盖模式仅对“已存在文件”执行先读后写约束；
                    # 新文件创建仍允许直接 write_file。
                    if not self._target_file_exists(target_path):
                        return None

                if self._has_read_file_in_conversation(target_path):
                    return None

                return {
                    "success": False,
                    "error": tr("tools_exec.read_before_edit_error", tool_name=tool_name),
                    "message": tr("tools_exec.read_before_edit_message", target_path=target_path),
                    "required_tool": "read_file",
                    "required_path": str(target_path),
                    "enforcement": "read_before_edit_required",
                }

    def _is_skill_enabled_for_enforcement(self, skill_id: str) -> bool:
                enabled_skill_ids = getattr(self, "enabled_skill_ids", None)
                if isinstance(enabled_skill_ids, set):
                    return skill_id in enabled_skill_ids
                if isinstance(enabled_skill_ids, (list, tuple)):
                    return skill_id in set(enabled_skill_ids)
                try:
                    config = load_personalization_config(self.data_dir)
                    catalog = get_skills_catalog(private_dir=infer_private_skills_dir(self.data_dir))
                    enabled = merge_enabled_skills(
                        config.get("enabled_skills") if isinstance(config, dict) else None,
                        catalog,
                        config.get("skills_catalog_snapshot") if isinstance(config, dict) else None,
                    )
                    return skill_id in set(enabled or [])
                except Exception:
                    return False

    def _required_skill_for_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Optional[str]:
                if tool_name in self._TERMINAL_SERIES_TOOLS and bool(getattr(self, "skill_strict_terminal_enabled", False)):
                    return "terminal-guide"
                if tool_name in self._SUB_AGENT_SERIES_TOOLS and bool(getattr(self, "skill_strict_sub_agent_enabled", False)):
                    return "sub-agent-guide"
                if (
                    tool_name == "run_command"
                ):
                    run_in_background = bool((arguments or {}).get("run_in_background", False))
                    if (
                        run_in_background
                        and bool(getattr(self, "skill_strict_run_command_background_enabled", False))
                    ):
                        return "run-command-guide"
                    if (
                        (not run_in_background)
                        and bool(getattr(self, "skill_strict_run_command_foreground_enabled", False))
                    ):
                        return "run-command-guide"
                return None

    def _check_skill_strict_prerequisite(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
                skill_id = self._required_skill_for_tool(tool_name, arguments)
                if not skill_id:
                    return None
                # 仅当该 skill 在个人空间中启用时才执行强约束
                if not self._is_skill_enabled_for_enforcement(skill_id):
                    return None
                already_read = bool(
                    self.context_manager._get_meta_flag(
                        self._skill_meta_key(skill_id),
                        False
                    )
                )
                if already_read:
                    return None
                required_path = f"{WORKSPACE_SKILLS_DIRNAME}/{skill_id}/SKILL.md"
                return {
                    "success": False,
                    "error": tr("tools_exec.skill_required_error", tool_name=tool_name, required_path=required_path),
                    "message": tr("tools_exec.skill_required_message", required_path=required_path),
                    "required_skill": skill_id,
                    "required_path": required_path,
                    "enforcement": "skill_read_required"
                }

    async def handle_tool_call(self, tool_name: str, arguments: Dict) -> str:
                """处理工具调用（添加参数预检查和改进错误处理）"""
                logger.debug("[handle_tool_call] 工具调用开始: tool_name=%s, arguments=%s", tool_name, arguments)
                try:
                    if hasattr(self, "_apply_execution_mode_to_runtime"):
                        self._apply_execution_mode_to_runtime()
                except Exception:
                    pass
                
                # 导入字符限制配置
                from config import (
                    MAX_READ_FILE_CHARS, 
                    MAX_RUN_COMMAND_CHARS, MAX_EXTRACT_WEBPAGE_CHARS
                )

                # 检查是否需要确认
                if tool_name in NEED_CONFIRMATION:
                    if not await self.confirm_action(tool_name, arguments):
                        return json.dumps({"success": False, "error": tr("tools_exec.action_cancelled")})

                # === 新增：预检查参数大小和格式 ===
                try:
                    # 检查参数总大小
                    arguments_str = json.dumps(arguments, ensure_ascii=False)
                    if len(arguments_str) > 200000:  # 200KB限制
                        return json.dumps({
                            "success": False,
                            "error": tr("tools_exec.params_too_large", chars=len(arguments_str)),
                            "suggestion": tr("tools_exec.params_too_large_suggestion")
                        }, ensure_ascii=False)

                    # 针对特定工具的内容检查
                    if tool_name == "write_file":
                        content = arguments.get("content", "")
                        length_limit = 200000
                        if not DISABLE_LENGTH_CHECK and len(content) > length_limit:
                            return json.dumps({
                                "success": False,
                                "error": tr("tools_exec.content_too_long", chars=len(content), limit=length_limit),
                                "suggestion": tr("tools_exec.content_too_long_suggestion")
                            }, ensure_ascii=False)
                        if '\\' in content and content.count('\\') > len(content) / 10:
                            print(f"{OUTPUT_FORMATS['warning']} 检测到大量转义字符，可能存在格式问题")

                except Exception as e:
                    return json.dumps({
                        "success": False,
                        "error": tr("tools_exec.precheck_failed", error=str(e))
                    }, ensure_ascii=False)

                # 自定义工具预解析（仅管理员）
                custom_tool = None
                if self.custom_tools_enabled and getattr(self, "user_role", "user") == "admin":
                    try:
                        self.custom_tool_registry.reload()
                    except Exception:
                        pass
                    custom_tool = self.custom_tool_registry.get_tool(tool_name)

                # MCP 工具 alias 识别（mcp__<server>__<tool>）
                is_mcp_tool_alias = bool(
                    isinstance(tool_name, str) and tool_name.startswith("mcp__")
                )
                if (tool_name == "list_mcp_servers" or is_mcp_tool_alias) and self._is_mcp_disabled_in_docker_mode():
                    blocked = self._mcp_disabled_message()
                    return json.dumps(
                        {
                            "success": False,
                            "error": blocked,
                            "message": blocked,
                        },
                        ensure_ascii=False,
                    )

                # Skill 强约束：未阅读对应 SKILL.md 时，阻止 terminal/sub-agent 系列工具执行
                strict_error = self._check_skill_strict_prerequisite(tool_name, arguments)
                if strict_error:
                    return json.dumps(strict_error, ensure_ascii=False)

                try:
                    if tool_name == "list_mcp_servers":
                        manager = getattr(self, "mcp_client_manager", None)
                        registry = getattr(self, "mcp_server_registry", None)
                        if manager is None or registry is None:
                            result = {"success": False, "error": tr("tools_exec.mcp_module_unavailable")}
                        else:
                            refresh = bool(arguments.get("refresh", False))
                            include_disabled = bool(arguments.get("include_disabled", False))
                            server_id = str(arguments.get("server_id") or "").strip() or None
                            sync_result = None
                            if refresh:
                                sync_result = await asyncio.to_thread(
                                    manager.sync_servers,
                                    server_id=server_id,
                                )
                            await asyncio.to_thread(registry.reload)
                            servers = registry.list_servers(include_disabled=include_disabled)
                            if server_id:
                                servers = [item for item in servers if item.get("id") == server_id]

                            # 读取当前 alias 映射（不强制再次发现）
                            _, alias_map = manager.build_llm_tools(ensure_discovery=False)
                            aliases_by_server = {}
                            for alias, binding in (alias_map or {}).items():
                                sid = getattr(binding, "server_id", "")
                                if not sid:
                                    continue
                                aliases_by_server.setdefault(sid, []).append(alias)

                            items = []
                            for item in servers:
                                sid = str(item.get("id") or "")
                                cache_tools = item.get("tools_cache") or []
                                cache_names = [
                                    str((tool or {}).get("name") or "").strip()
                                    for tool in cache_tools
                                    if isinstance(tool, dict)
                                ]
                                cache_names = [name for name in cache_names if name]
                                aliases = sorted(aliases_by_server.get(sid, []))
                                items.append(
                                    {
                                        "id": sid,
                                        "name": item.get("name") or sid,
                                        "enabled": bool(item.get("enabled", True)),
                                        "transport": item.get("transport"),
                                        "timeout_seconds": item.get("timeout_seconds"),
                                        "tools_cache_count": len(cache_names),
                                        "tools_cache_names": cache_names,
                                        "tool_aliases": aliases,
                                        "tools_cache_updated_at": item.get("tools_cache_updated_at"),
                                        "last_error": item.get("last_error") or "",
                                    }
                                )

                            result = {
                                "success": True,
                                "count": len(items),
                                "servers": items,
                                "sync_result": sync_result,
                            }
                    elif is_mcp_tool_alias:
                        if not getattr(self, "mcp_tools_enabled", False):
                            result = {"success": False, "error": tr("tools_exec.mcp_not_enabled")}
                            return json.dumps(result, ensure_ascii=False)
                        manager = getattr(self, "mcp_client_manager", None)
                        if manager is None:
                            result = {"success": False, "error": tr("tools_exec.mcp_manager_unavailable")}
                        else:
                            # intent 仅用于原生工具的人类可读展示，不应透传给 MCP 远端工具
                            mcp_arguments = arguments
                            if isinstance(arguments, dict) and "intent" in arguments:
                                mcp_arguments = dict(arguments)
                                mcp_arguments.pop("intent", None)
                            result = await asyncio.to_thread(
                                manager.call_tool_by_alias,
                                tool_name,
                                mcp_arguments,
                                alias_map=getattr(self, "mcp_tool_alias_map", None),
                            )
                    elif custom_tool:
                        result = await self.custom_tool_executor.run(tool_name, arguments)
                    elif tool_name == "read_file":
                        result = self._handle_read_tool(arguments)
                        self._mark_skill_read_from_result(tool_name, arguments, result)
                        self._mark_file_read_from_result(tool_name, arguments, result)
                    elif tool_name == "read_skill":
                        result = self._handle_read_skill_tool(arguments)
                        self._mark_skill_read_from_result(tool_name, arguments, result)
                        self._mark_file_read_from_result(tool_name, arguments, result)
                    elif tool_name == "create_skill":
                        result = self._handle_create_skill_tool(arguments)
                    elif tool_name == "activate_workflow":
                        result = self._handle_activate_workflow_tool(arguments)
                    elif tool_name == "get_workflow_status":
                        result = self._handle_get_workflow_status_tool(arguments)
                    elif tool_name == "list_workflows":
                        result = self._handle_list_workflows_tool(arguments)
                    elif tool_name == "save_workflow":
                        result = self._handle_save_workflow_tool(arguments)
                    elif tool_name in {"vlm_analyze", "ocr_image"}:
                        path = arguments.get("path")
                        prompt = arguments.get("prompt")
                        if not path:
                            return json.dumps({"success": False, "error": tr("tools_exec.missing_path_param"), "warnings": []}, ensure_ascii=False)
                        result = self.ocr_client.vlm_analyze(path=path, prompt=prompt or "")
                    elif tool_name == "view_image":
                        path = (arguments.get("path") or "").strip()
                        if not path:
                            return json.dumps({"success": False, "error": tr("tools_exec.path_empty")}, ensure_ascii=False)
                        host_unrestricted = self._is_host_mode()
                        if path.startswith("/workspace"):
                            if host_unrestricted:
                                path = path.split("/workspace", 1)[1].lstrip("/")
                            else:
                                return json.dumps({"success": False, "error": tr("tools_exec.invalid_path_no_workspace")}, ensure_ascii=False)
                        if host_unrestricted and (Path(path).is_absolute() or (len(path) > 1 and path[1] == ":")):
                            abs_path = Path(path).expanduser().resolve()
                        else:
                            abs_path = (Path(self.context_manager.project_path) / path).resolve()
                            if not host_unrestricted:
                                try:
                                    abs_path.relative_to(Path(self.context_manager.project_path).resolve())
                                except Exception:
                                    return json.dumps({"success": False, "error": tr("tools_exec.invalid_path_no_workspace")}, ensure_ascii=False)
                        if not abs_path.exists() or not abs_path.is_file():
                            return json.dumps({"success": False, "error": tr("tools_exec.image_not_found", path=path)}, ensure_ascii=False)
                        if abs_path.stat().st_size > 10 * 1024 * 1024:
                            return json.dumps({"success": False, "error": tr("tools_exec.image_too_large")}, ensure_ascii=False)
                        allowed_ext = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
                        if abs_path.suffix.lower() not in allowed_ext:
                            return json.dumps({"success": False, "error": tr("tools_exec.image_unsupported_format", suffix=abs_path.suffix)}, ensure_ascii=False)
                        # 记录待附加图片，供上层将图片附加到工具结果
                        self.pending_image_view = {
                            "path": str(path)
                        }
                        # size 供前端美化展示（工具结果 meta 区）；
                        # 原图由前端按 path 走 /api/file/content 加载，不在结果里内联
                        result = {
                            "success": True,
                            "message": tr("tools_exec.image_attached"),
                            "path": path,
                            "size": abs_path.stat().st_size,
                        }
                    elif tool_name == "view_video":
                        path = (arguments.get("path") or "").strip()
                        if not path:
                            return json.dumps({"success": False, "error": tr("tools_exec.path_empty")}, ensure_ascii=False)
                        host_unrestricted = self._is_host_mode()
                        if path.startswith("/workspace"):
                            if host_unrestricted:
                                path = path.split("/workspace", 1)[1].lstrip("/")
                            else:
                                return json.dumps({"success": False, "error": tr("tools_exec.invalid_path_relative")}, ensure_ascii=False)
                        if host_unrestricted and (Path(path).is_absolute() or (len(path) > 1 and path[1] == ":")):
                            abs_path = Path(path).expanduser().resolve()
                        else:
                            abs_path = (Path(self.context_manager.project_path) / path).resolve()
                            if not host_unrestricted:
                                try:
                                    abs_path.relative_to(Path(self.context_manager.project_path).resolve())
                                except Exception:
                                    return json.dumps({"success": False, "error": tr("tools_exec.invalid_path_relative")}, ensure_ascii=False)
                        if not abs_path.exists() or not abs_path.is_file():
                            return json.dumps({"success": False, "error": tr("tools_exec.video_not_found", path=path)}, ensure_ascii=False)
                        allowed_ext = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
                        if abs_path.suffix.lower() not in allowed_ext:
                            return json.dumps({"success": False, "error": tr("tools_exec.video_unsupported_format", suffix=abs_path.suffix)}, ensure_ascii=False)
                        if abs_path.stat().st_size > 50 * 1024 * 1024:
                            return json.dumps({"success": False, "error": tr("tools_exec.video_too_large")}, ensure_ascii=False)
                        self.pending_video_view = {"path": str(path)}
                        result = {
                            "success": True,
                            "message": tr("tools_exec.video_attached"),
                            "path": path
                        }

                    # 终端会话管理工具
                    elif tool_name == "terminal_session":
                        action = arguments["action"]

                        if action == "open":
                            result = self.terminal_manager.open_terminal(
                                session_name=arguments.get("session_name", "default"),
                                working_dir=arguments.get("working_dir"),
                                make_active=True
                            )
                            if result["success"]:
                                print(f"{OUTPUT_FORMATS['session']} 终端会话已打开: {arguments.get('session_name', 'default')}")

                        elif action == "close":
                            result = self.terminal_manager.close_terminal(
                                session_name=arguments.get("session_name", "default")
                            )
                            if result["success"]:
                                print(f"{OUTPUT_FORMATS['session']} 终端会话已关闭: {arguments.get('session_name', 'default')}")

                        elif action == "list":
                            result = self.terminal_manager.list_terminals()

                        elif action == "reset":
                            result = self.terminal_manager.reset_terminal(
                                session_name=arguments.get("session_name")
                            )
                            if result["success"]:
                                print(f"{OUTPUT_FORMATS['session']} 终端会话已重置: {result['session']}")

                        else:
                            result = {"success": False, "error": tr("tools_exec.unknown_action", action=action)}
                        result["action"] = action

                    # 终端输入工具
                    elif tool_name == "terminal_input":
                        output_wait = arguments.get("output_wait")
                        if output_wait is None:
                            output_wait = arguments.get("timeout")
                        result = self.terminal_manager.send_to_terminal(
                            command=arguments["command"],
                            session_name=arguments.get("session_name"),
                            output_wait=output_wait
                        )
                        if result["success"]:
                            print(f"{OUTPUT_FORMATS['terminal']} 执行命令: {arguments['command']}")

                    elif tool_name == "terminal_snapshot":
                        result = self.terminal_manager.get_terminal_snapshot(
                            session_name=arguments.get("session_name"),
                            lines=arguments.get("lines"),
                            max_chars=arguments.get("max_chars")
                        )

                    # sleep工具
                    elif tool_name == "sleep":
                        seconds = arguments.get("seconds")
                        wait_sub_agent_ids = arguments.get("wait_sub_agent_ids")
                        wait_sub_agent_output = arguments.get("wait_sub_agent_output")
                        wait_runcommand_id = arguments.get("wait_runcommand_id")
                        reason = arguments.get("reason", tr("tools_exec.sleep_reason_default"))

                        provided = 0
                        if seconds is not None:
                            provided += 1
                        if wait_sub_agent_ids:
                            provided += 1
                        if wait_sub_agent_output:
                            provided += 1
                        if wait_runcommand_id:
                            provided += 1
                        if provided == 0:
                            result = {
                                "success": False,
                                "error": tr("tools_exec.sleep_no_params")
                            }
                        elif provided > 1:
                            result = {
                                "success": False,
                                "error": tr("tools_exec.sleep_params_exclusive")
                            }
                        elif wait_sub_agent_output:
                            if not getattr(self, "multi_agent_mode", False):
                                result = {"success": False, "error": tr("tools_exec.agent_output_multi_agent_only")}
                            else:
                                display_name = str(wait_sub_agent_output or "").strip()
                                manager = getattr(self, "sub_agent_manager", None)
                                if not manager:
                                    result = {"success": False, "error": tr("tools_exec.sub_agent_manager_unavailable")}
                                else:
                                    state = manager.get_multi_agent_state(getattr(self.context_manager, "current_conversation_id", None))
                                    if not state:
                                        result = {"success": False, "error": tr("tools_exec.no_multi_agent_state")}
                                    else:
                                        # 显示名寻址：模型只传显示名，内部解析为全局 agent_id
                                        inst = state.get_instance_by_display_name(display_name)
                                        if not inst:
                                            available = tr("tools_exec.list_sep").join(state.list_display_names()) or tr("tools_exec.none_placeholder")
                                            result = {"success": False, "error": tr("tools_exec.agent_not_found", display_name=display_name, available=available)}
                                        else:
                                            try:
                                                loop = asyncio.get_running_loop()
                                                fut = state.register_output_wait(inst.agent_id, loop)
                                                msg = await asyncio.wait_for(fut, timeout=300)
                                                result = {
                                                    "success": True,
                                                    "mode": "wait_sub_agent_output",
                                                    "display_name": inst.display_name,
                                                    "message": msg,
                                                }
                                            except asyncio.TimeoutError:
                                                result = {"success": False, "error": tr("tools_exec.agent_output_wait_timeout", display_name=inst.display_name)}
                                            except asyncio.CancelledError:
                                                result = {"success": False, "error": tr("tools_exec.agent_output_wait_cancelled", display_name=inst.display_name)}
                                            except RuntimeError as exc:
                                                error_msg = str(exc)
                                                if "send_message_to_sub_agent" not in error_msg:
                                                    error_msg += tr("tools_exec.agent_reactivate_hint")
                                                result = {"success": False, "error": error_msg}
                                            except Exception as exc:
                                                result = {"success": False, "error": tr("tools_exec.agent_output_wait_failed", display_name=inst.display_name, error=str(exc))}
                        elif wait_sub_agent_ids:
                            if getattr(self, "multi_agent_mode", False):
                                result = {
                                    "success": False,
                                    "error": tr("tools_exec.sleep_wait_ids_not_supported")
                                }
                            elif not isinstance(wait_sub_agent_ids, list) or not wait_sub_agent_ids:
                                result = {"success": False, "error": tr("tools_exec.wait_ids_nonempty")}
                            else:
                                normalized_ids = []
                                invalid = []
                                for item in wait_sub_agent_ids:
                                    try:
                                        agent_id = int(item)
                                        if agent_id <= 0:
                                            raise ValueError()
                                        normalized_ids.append(agent_id)
                                    except Exception:
                                        invalid.append(item)
                                if invalid:
                                    result = {"success": False, "error": tr("tools_exec.wait_ids_invalid", invalid=invalid)}
                                else:
                                    manager = getattr(self, "sub_agent_manager", None)
                                    if not manager:
                                        result = {"success": False, "error": tr("tools_exec.sub_agent_manager_unavailable")}
                                    else:
                                        task_ids = []
                                        missing = []
                                        for aid in normalized_ids:
                                            task = manager.lookup_task(agent_id=aid)
                                            if not task:
                                                missing.append(aid)
                                            else:
                                                task_ids.append(task.get("task_id"))
                                        if missing:
                                            result = {"success": False, "error": tr("tools_exec.agent_not_found_by_id", missing=missing)}
                                        else:
                                            wait_results = []
                                            waited_task_ids = []
                                            for tid in task_ids:
                                                wait_result = await asyncio.to_thread(
                                                    manager.wait_for_completion,
                                                    task_id=tid,
                                                    timeout_seconds=None,
                                                )
                                                wait_results.append(wait_result)
                                                waited_task_ids.append(tid)
                                                try:
                                                    task = manager.tasks.get(tid)
                                                    if isinstance(task, dict):
                                                        task["notified"] = True
                                                        task["updated_at"] = time.time()
                                                except Exception:
                                                    pass
                                            try:
                                                manager._save_state()
                                            except Exception:
                                                pass
                                            result = {
                                                "success": True,
                                                "mode": "wait_sub_agent_ids",
                                                "agent_ids": normalized_ids,
                                                "waited_task_ids": waited_task_ids,
                                                "results": wait_results,
                                                "message": tr("tools_exec.waited_agents_done", count=len(normalized_ids))
                                            }
                                            try:
                                                if not hasattr(self, "_announced_sub_agent_tasks"):
                                                    self._announced_sub_agent_tasks = set()
                                                for tid in waited_task_ids:
                                                    if tid:
                                                        self._announced_sub_agent_tasks.add(tid)
                                            except Exception:
                                                pass
                        elif wait_runcommand_id:
                            bg_manager = getattr(self, "background_command_manager", None)
                            if not bg_manager:
                                result = {"success": False, "error": tr("tools_exec.background_manager_unavailable")}
                            else:
                                rec = bg_manager.get_record(str(wait_runcommand_id))
                                current_conv = getattr(self.context_manager, "current_conversation_id", None)
                                if not rec:
                                    result = {"success": False, "error": tr("tools_exec.bg_command_not_found", command_id=wait_runcommand_id)}
                                elif rec.get("conversation_id") and current_conv and rec.get("conversation_id") != current_conv:
                                    result = {"success": False, "error": tr("tools_exec.bg_command_wrong_conversation")}
                                else:
                                    wait_result = await asyncio.to_thread(
                                        bg_manager.wait_for_completion,
                                        str(wait_runcommand_id),
                                        None,
                                        True,
                                    )
                                    bg_manager.mark_claimed(str(wait_runcommand_id))
                                    result = {
                                        "success": bool(wait_result.get("success", False)),
                                        "mode": "wait_runcommand_id",
                                        "command_id": str(wait_runcommand_id),
                                        "result": wait_result,
                                        "message": tr("tools_exec.bg_runcommand_done")
                                    }
                        else:
                            max_sleep = 3600  # 最多等待3600秒（1小时）
                            seconds_parse_ok = True
                            try:
                                seconds = float(seconds)
                            except Exception:
                                result = {"success": False, "error": tr("tools_exec.seconds_not_number")}
                                seconds_parse_ok = False
                                seconds = 0.0
                            if seconds_parse_ok and seconds > max_sleep:
                                result = {
                                    "success": False,
                                    "error": tr("tools_exec.sleep_too_long", max_sleep=max_sleep),
                                    "suggestion": tr("tools_exec.sleep_too_long_suggestion")
                                }
                            elif seconds_parse_ok:
                                # 确保秒数为正数
                                if seconds <= 0:
                                    result = {
                                        "success": False,
                                        "error": tr("tools_exec.sleep_must_be_positive")
                                    }
                                else:
                                    print(f"{OUTPUT_FORMATS['info']} 等待 {seconds} 秒: {reason}")
                                    await asyncio.sleep(seconds)
                                    result = {
                                        "success": True,
                                        "message": tr("tools_exec.waited_seconds", seconds=seconds),
                                        "reason": reason,
                                        "timestamp": datetime.now().isoformat()
                                    }
                                    print(f"{OUTPUT_FORMATS['success']} 等待完成")

                    elif tool_name == "create_file":
                        result = self.file_manager.create_file(
                            path=arguments["path"],
                            file_type=arguments["file_type"]
                        )
                        if isinstance(result, dict) and result.get("success"):
                            # create_file 新建文件后，视为当前会话已“接触”该文件，
                            # 避免后续 write_file/edit_file 被“先 read_file 再编辑”误拦截。
                            self._mark_file_as_read_visited(result.get("path") or arguments.get("path"))
                        # 添加备注
                        if result["success"] and arguments.get("annotation"):
                            self.context_manager.update_annotation(
                                result["path"],
                                arguments["annotation"]
                            )
                        if result.get("success"):
                            result["message"] = tr(
                                "tools_exec.file_created_empty", path=result['path']
                            )

                    elif tool_name == "delete_file":
                        result = self.file_manager.delete_file(arguments["path"])
                        # 如果删除成功，同时删除备注
                        if result.get("success") and result.get("action") == "deleted":
                            deleted_path = result.get("path")
                            # 快捷窗口：从编辑文件记录中移除
                            self._remove_edited_file(deleted_path)
                            # 编辑摘要：同步移除该文件
                            self._remove_edit_summary(deleted_path)
                            # 删除备注
                            if deleted_path in self.context_manager.file_annotations:
                                del self.context_manager.file_annotations[deleted_path]
                                self.context_manager.save_annotations()
                                print(f"🧹 已删除文件备注: {deleted_path}")

                    elif tool_name == "rename_file":
                        result = self.file_manager.rename_file(
                            arguments["old_path"],
                            arguments["new_path"]
                        )
                        # 如果重命名成功，更新备注和聚焦的key
                        # 如果重命名成功，更新备注
                        if result.get("success") and result.get("action") == "renamed":
                            old_path = result.get("old_path")
                            new_path = result.get("new_path")
                            # 快捷窗口：同步编辑文件记录中的路径
                            self._rename_edited_file(old_path, new_path)
                            # 编辑摘要：同步路径
                            self._rename_edit_summary(old_path, new_path)
                            # 更新备注
                            if old_path in self.context_manager.file_annotations:
                                annotation = self.context_manager.file_annotations[old_path]
                                del self.context_manager.file_annotations[old_path]
                                self.context_manager.file_annotations[new_path] = annotation
                                self.context_manager.save_annotations()
                                print(f"📝 已更新文件备注: {old_path} -> {new_path}")

                    elif tool_name == "write_file":
                        read_guard_error = self._check_read_before_edit_prerequisite(tool_name, arguments)
                        if read_guard_error:
                            return json.dumps(read_guard_error, ensure_ascii=False)
                        path = arguments.get("file_path")
                        content = arguments.get("content", "")
                        append_flag = bool(arguments.get("append", False))
                        if not path:
                            result = {"success": False, "error": tr("tools_exec.missing_file_path")}
                        else:
                            # 在写入前先备份当前内容（浅备份）。
                            self._track_shallow_versioning(path)
                            mode = "a" if append_flag else "w"
                            result = self.file_manager.write_file(path, content, mode=mode)
                            if isinstance(result, dict) and result.get("success"):
                                # write_file 成功后，该文件视作当前会话已“接触”，
                                # 允许后续继续 write/edit 而不再触发先读拦截。
                                self._mark_file_as_read_visited(result.get("path") or path)
                                # 快捷窗口：记录本次对话写入过的文件
                                self._record_edited_file(result.get("path") or path, "write")
                                # 编辑摘要：写入合并 diff（original_file/new_file 为写前/后全文）
                                self._update_edit_summary(
                                    result.get("path") or path,
                                    result.get("original_file"),
                                    result.get("new_file"),
                                )

                    elif tool_name == "edit_file":
                        read_guard_error = self._check_read_before_edit_prerequisite(tool_name, arguments)
                        if read_guard_error:
                            return json.dumps(read_guard_error, ensure_ascii=False)
                        path = arguments.get("file_path")
                        replacements = arguments.get("replacements")
                        if not path:
                            result = {"success": False, "error": tr("tools_exec.missing_file_path")}
                        elif not isinstance(replacements, list) or not replacements:
                            result = {"success": False, "error": tr("tools_exec.missing_replacements")}
                        else:
                            # 在替换前先备份当前内容（浅备份）。
                            self._track_shallow_versioning(path)
                            result = self.file_manager.replace_many_in_file(path, replacements)
                            if isinstance(result, dict) and result.get("success"):
                                self._mark_file_as_read_visited(result.get("path") or path)
                                # 快捷窗口：记录本次对话编辑过的文件
                                self._record_edited_file(result.get("path") or path, "edit")
                                # 编辑摘要：写入合并 diff（original_file/new_file 为改前/后全文）
                                self._update_edit_summary(
                                    result.get("path") or path,
                                    result.get("original_file"),
                                    result.get("new_file"),
                                )
                    elif tool_name == "create_folder":
                        result = self.file_manager.create_folder(arguments["path"])

                    elif tool_name == "web_search":
                        allowed, quota_info = self.record_search_call()
                        if not allowed:
                            return json.dumps({
                                "success": False,
                                "error": tr("tools_exec.search_quota_exhausted", reset_at=quota_info.get('reset_at')),
                                "quota": quota_info
                            }, ensure_ascii=False)
                        search_response = await self.search_engine.search_with_summary(
                            query=arguments["query"],
                            max_results=arguments.get("max_results"),
                            topic=arguments.get("topic"),
                            time_range=arguments.get("time_range"),
                            days=arguments.get("days"),
                            start_date=arguments.get("start_date"),
                            end_date=arguments.get("end_date"),
                            country=arguments.get("country"),
                            include_domains=arguments.get("include_domains")
                        )

                        if search_response["success"]:
                            result = {
                                "success": True,
                                "summary": search_response["summary"],
                                "filters": search_response.get("filters", {}),
                                "query": search_response.get("query"),
                                "results": search_response.get("results", []),
                                "total_results": search_response.get("total_results", 0)
                            }
                        else:
                            result = {
                                "success": False,
                                "error": search_response.get("error", tr("tools_exec.search_failed")),
                                "filters": search_response.get("filters", {}),
                                "query": search_response.get("query"),
                                "results": search_response.get("results", []),
                                "total_results": search_response.get("total_results", 0)
                            }

                    elif tool_name == "extract_webpage":
                        url = arguments["url"]
                        try:
                            # 从config获取API密钥
                            from config import TAVILY_API_KEY
                            full_content, _ = await extract_webpage_content(
                                urls=url, 
                                api_key=TAVILY_API_KEY,
                                extract_depth="basic",
                                max_urls=1
                            )

                            # 字符数检查
                            char_count = len(full_content)
                            if char_count > MAX_EXTRACT_WEBPAGE_CHARS:
                                result = {
                                    "success": False,
                                    "error": tr("tools_exec.webpage_extract_too_long", char_count=char_count),
                                    "char_count": char_count,
                                    "limit": MAX_EXTRACT_WEBPAGE_CHARS,
                                    "url": url
                                }
                            else:
                                result = {
                                    "success": True,
                                    "url": url,
                                    "content": full_content
                                }
                        except Exception as e:
                            result = {
                                "success": False,
                                "error": tr("tools_exec.webpage_extract_failed", error=str(e)),
                                "url": url
                            }

                    elif tool_name == "save_webpage":
                        url = arguments["url"]
                        target_path = arguments["target_path"]
                        try:
                            from config import TAVILY_API_KEY
                        except ImportError:
                            TAVILY_API_KEY = None

                        if not TAVILY_API_KEY or TAVILY_API_KEY == "your-tavily-api-key":
                            result = {
                                "success": False,
                                "error": tr("tools_exec.tavily_key_missing"),
                                "url": url,
                                "path": target_path
                            }
                        else:
                            try:
                                extract_result = await tavily_extract(
                                    urls=url,
                                    api_key=TAVILY_API_KEY,
                                    extract_depth="basic",
                                    max_urls=1
                                )

                                if not extract_result or "error" in extract_result:
                                    error_message = extract_result.get("error", tr("tools_exec.extract_failed_no_content")) if isinstance(extract_result, dict) else tr("tools_exec.extract_failed")
                                    result = {
                                        "success": False,
                                        "error": error_message,
                                        "url": url,
                                        "path": target_path
                                    }
                                else:
                                    results_list = extract_result.get("results", []) if isinstance(extract_result, dict) else []

                                    primary_result = None
                                    for item in results_list:
                                        if item.get("raw_content"):
                                            primary_result = item
                                            break
                                    if primary_result is None and results_list:
                                        primary_result = results_list[0]

                                    if not primary_result:
                                        failed_list = extract_result.get("failed_results", []) if isinstance(extract_result, dict) else []
                                        result = {
                                            "success": False,
                                            "error": tr("tools_exec.extract_result_empty"),
                                            "url": url,
                                            "path": target_path,
                                            "failed": failed_list
                                        }
                                    else:
                                        content_to_save = primary_result.get("raw_content") or primary_result.get("content") or ""

                                        if not content_to_save:
                                            result = {
                                                "success": False,
                                                "error": tr("tools_exec.webpage_content_empty"),
                                                "url": url,
                                                "path": target_path
                                            }
                                        else:
                                            write_result = self.file_manager.write_file(target_path, content_to_save, mode="w")

                                            if not write_result.get("success"):
                                                result = {
                                                    "success": False,
                                                    "error": write_result.get("error", tr("tools_exec.write_file_failed")),
                                                    "url": url,
                                                    "path": target_path
                                                }
                                            else:
                                                char_count = len(content_to_save)
                                                byte_size = len(content_to_save.encode("utf-8"))
                                                result = {
                                                    "success": True,
                                                    "url": url,
                                                    "path": write_result.get("path", target_path),
                                                    "char_count": char_count,
                                                    "byte_size": byte_size,
                                                    "message": tr("tools_exec.webpage_saved", path=write_result.get('path', target_path))
                                                }

                                                if isinstance(extract_result, dict) and extract_result.get("failed_results"):
                                                    result["warnings"] = extract_result["failed_results"]

                            except Exception as e:
                                result = {
                                    "success": False,
                                    "error": tr("tools_exec.webpage_save_failed", error=str(e)),
                                    "url": url,
                                    "path": target_path
                                }

                    elif tool_name == "run_command":
                        permission_mode = "unrestricted"
                        try:
                            permission_mode = self.get_permission_mode()
                        except Exception:
                            permission_mode = str(getattr(self, "current_permission_mode", "unrestricted") or "unrestricted")
                        write_granted_once = bool(arguments.get("_approval_write_granted", False))
                        sandbox_write_access = not (
                            permission_mode == "readonly"
                            or (permission_mode in {"approval", "auto_approval"} and not write_granted_once)
                        )
                        network_granted_once = bool(arguments.get("_approval_network_granted", False))
                        if network_granted_once:
                            network_permission = "full"
                        else:
                            network_permission = getattr(
                                self, "host_network_permission", None
                            ) or os.environ.get("HOST_SANDBOX_NETWORK_PERMISSION", "restricted")
                        run_in_background = bool(arguments.get("run_in_background", False))
                        timeout_value = arguments.get("timeout")
                        if run_in_background:
                            if timeout_value is None or float(timeout_value) <= 0:
                                result = {
                                    "success": False,
                                    "error": tr("tools_exec.bg_timeout_required")
                                }
                            elif float(timeout_value) > 3600:
                                result = {
                                    "success": False,
                                    "error": tr("tools_exec.bg_timeout_max")
                                }
                            else:
                                bg_manager = getattr(self, "background_command_manager", None)
                                if not bg_manager:
                                    result = {"success": False, "error": tr("tools_exec.background_manager_unavailable")}
                                else:
                                    result = bg_manager.create_background_command(
                                        terminal_ops=self.terminal_ops,
                                        command=arguments["command"],
                                        timeout=timeout_value,
                                        conversation_id=getattr(self.context_manager, "current_conversation_id", None),
                                        wait_seconds=5.0,
                                        network_permission=network_permission,
                                        sandbox_write_access=sandbox_write_access,
                                    )
                        else:
                            if timeout_value is None or float(timeout_value) <= 0:
                                result = {
                                    "success": False,
                                    "error": tr("tools_exec.timeout_required")
                                }
                            elif float(timeout_value) > 120:
                                result = {
                                    "success": False,
                                    "error": tr("tools_exec.fg_timeout_max")
                                }
                            else:
                                result = await self.terminal_ops.run_command(
                                    arguments["command"],
                                    timeout=timeout_value,
                                    sandbox_write_access=sandbox_write_access,
                                    network_permission=network_permission,
                                )

                                # 字符数检查
                                if result.get("success") and "output" in result:
                                    char_count = len(result["output"])
                                    if char_count > MAX_RUN_COMMAND_CHARS:
                                        result = {
                                            "success": False,
                                            "error": tr("tools_exec.result_too_large", char_count=char_count),
                                            "char_count": char_count,
                                            "limit": MAX_RUN_COMMAND_CHARS,
                                            "command": arguments["command"]
                                        }

                    elif tool_name == "update_memory":
                        operation = arguments["operation"]
                        content = arguments.get("content")
                        index = arguments.get("index")
                        enriched_content = content
                        if operation in {"append", "replace"} and content and str(content).strip():
                            current_conversation_id = getattr(
                                getattr(self, "context_manager", None),
                                "current_conversation_id",
                                None,
                            ) or "unknown"
                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                            text = str(content).strip()
                            if not text.startswith("["):
                                enriched_content = f"[{timestamp}][{current_conversation_id}] {text}"

                        # 参数校验
                        if operation == "append" and (not content or not str(content).strip()):
                            result = {"success": False, "error": tr("tools_exec.append_needs_content")}
                        elif operation == "replace" and (index is None or index <= 0 or not content or not str(content).strip()):
                            result = {"success": False, "error": tr("tools_exec.replace_needs_valid_index_content")}
                        elif operation == "delete" and (index is None or index <= 0):
                            result = {"success": False, "error": tr("tools_exec.delete_needs_valid_index")}
                        else:
                            # 统一使用 main 记忆类型
                            result = self.memory_manager.update_entries(
                                memory_type="main",
                                operation=operation,
                                content=enriched_content,
                                index=index
                            )

                    elif tool_name == "recall_project_memory":
                        name = str(arguments.get("name", "")).strip()
                        if not name:
                            result = {"success": False, "error": tr("tools_exec.recall_needs_name")}
                        else:
                            result = self._handle_recall_project_memory(name)

                    elif tool_name == "search_project_memory":
                        raw_keywords = arguments.get("keywords")
                        if isinstance(raw_keywords, list):
                            keywords = [
                                str(item).strip()
                                for item in raw_keywords
                                if str(item or "").strip()
                            ][:5]
                        else:
                            keywords = []
                        if not keywords:
                            result = {"success": False, "error": tr("tools_exec.search_memory_needs_keywords")}
                        else:
                            try:
                                max_results = int(arguments.get("max_results") or 5)
                            except Exception:
                                max_results = 5
                            result = self._handle_search_project_memory(keywords, max_results)

                    elif tool_name == "update_project_memory":
                        name = str(arguments.get("name", "")).strip()
                        description = str(arguments.get("description", "")).strip()
                        content_text = str(arguments.get("content", "")).strip()
                        if not name:
                            result = {"success": False, "error": tr("tools_exec.update_memory_needs_name")}
                        elif not description:
                            result = {"success": False, "error": tr("tools_exec.update_memory_needs_description")}
                        elif not content_text:
                            result = {"success": False, "error": tr("tools_exec.update_memory_needs_content")}
                        else:
                            result = self._handle_update_project_memory(name, description, content_text)

                    elif tool_name == "conversation_search":
                        query = str(arguments.get("query") or "").strip()
                        raw_keywords = arguments.get("keywords")
                        if isinstance(raw_keywords, list):
                            keywords = [
                                str(item).strip()
                                for item in raw_keywords
                                if str(item or "").strip()
                            ][:3]
                        else:
                            keywords = []
                        start_date = str(arguments.get("start_date") or "").strip() or None
                        end_date = str(arguments.get("end_date") or "").strip() or None
                        try:
                            limit = int(arguments.get("limit") or 10)
                        except Exception:
                            limit = 10
                        limit = max(1, min(limit, 100))
                        manager = getattr(self.context_manager, "conversation_manager", None)
                        current_conversation_id = getattr(self.context_manager, "current_conversation_id", None)
                        if not current_conversation_id and manager:
                            current_conversation_id = getattr(manager, "current_conversation_id", None)
                        items = manager.search_conversation_summaries(
                            query=query,
                            keywords=keywords,
                            start_date=start_date,
                            end_date=end_date,
                            limit=limit,
                            first_message_max_chars=100,
                            exclude_conversation_id=current_conversation_id,
                        ) if manager else []
                        result = {
                            "success": True,
                            "query": query,
                            "keywords": keywords,
                            "start_date": start_date,
                            "end_date": end_date,
                            "limit": limit,
                            "excluded_conversation_id": current_conversation_id,
                            "results": items,
                            "count": len(items),
                            "summary": tr("tools_exec.conversation_search_found", count=len(items)),
                        }

                    elif tool_name == "conversation_review":
                        conversation_id = str(arguments.get("conversation_id") or "").strip()
                        review_mode = str(arguments.get("mode") or "").strip().lower()
                        if not conversation_id:
                            result = {"success": False, "error": tr("tools_exec.conversation_id_empty")}
                        elif review_mode not in {"read", "save"}:
                            result = {"success": False, "error": tr("tools_exec.review_mode_invalid"), "conversation_id": conversation_id}
                        else:
                            manager = getattr(self.context_manager, "conversation_manager", None)
                            conversation_data = manager.load_conversation(conversation_id) if manager else None
                            if not conversation_data:
                                result = {
                                    "success": False,
                                    "error": tr("tools_exec.review_conversation_missing"),
                                    "conversation_id": conversation_id,
                                }
                            else:
                                from server.utils_common import build_review_lines, _sanitize_filename_component

                                messages = conversation_data.get("messages", [])
                                content = "\n".join(build_review_lines(messages)) + "\n"
                                title = conversation_data.get("title") or "untitled"
                                char_count = len(content)

                                def save_review_file() -> str:
                                    safe_title = _sanitize_filename_component(title)
                                    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                                    review_dir = Path(self.project_path) / WORKSPACE_REVIEW_DIRNAME
                                    review_dir.mkdir(parents=True, exist_ok=True)
                                    filename = f"review_{safe_title}_{timestamp}.md"
                                    target = review_dir / filename
                                    target.write_text(content, encoding="utf-8")
                                    return f"{WORKSPACE_REVIEW_DIRNAME}/{filename}"

                                if review_mode == "read" and char_count <= 50000:
                                    result = {
                                        "success": True,
                                        "mode": "read",
                                        "conversation_id": conversation_id,
                                        "title": title,
                                        "content": content,
                                        "char_count": char_count,
                                        "summary": tr("tools_exec.review_returned", char_count=char_count),
                                    }
                                else:
                                    rel_path = save_review_file()
                                    too_long = review_mode == "read" and char_count > 50000
                                    result = {
                                        "success": True,
                                        "mode": review_mode,
                                        "conversation_id": conversation_id,
                                        "title": title,
                                        "path": rel_path,
                                        "char_count": char_count,
                                        "too_long": too_long,
                                        "summary": (
                                            tr("tools_exec.review_too_long_saved", char_count=char_count, rel_path=rel_path)
                                            if too_long
                                            else tr("tools_exec.review_saved", rel_path=rel_path)
                                        ),
                                    }

                    elif tool_name == "todo_create":
                        result = self.todo_manager.create_todo_list(
                            overview=arguments.get("overview", ""),
                            tasks=arguments.get("tasks", [])
                        )

                    elif tool_name == "todo_update_task":
                        task_indices = arguments.get("task_indices")
                        if task_indices is None:
                            task_indices = arguments.get("task_index")
                        result = self.todo_manager.update_task_status(
                            task_indices=task_indices,
                            completed=arguments.get("completed", True)
                        )

                    elif tool_name == "create_sub_agent":
                        # 多智能体模式：create_sub_agent 走新签名，需要 role_id/display_name/multi_agent_mode
                        if getattr(self, "multi_agent_mode", False):
                            role_id = arguments.get("role_id")
                            if not role_id:
                                result = {"success": False, "error": tr("tools_exec.create_sub_agent_need_role_id")}
                            elif arguments.get("deliverables_dir"):
                                result = {"success": False, "error": tr("tools_exec.create_sub_agent_no_deliverables")}
                            else:
                                try:
                                    from modules.multi_agent.role_store import load_preset_role, infer_custom_roles_dir
                                    from modules.multi_agent.prompts import build_multi_agent_sub_agent_prompt
                                    _data_dir = str(getattr(self, "data_dir", ""))
                                    _custom_dir = infer_custom_roles_dir(_data_dir)
                                    # host模式下 custom_dir 就是 runtime_dir；web模式下 runtime_dir 指向 web 预设目录
                                    # Windows 下 data_dir 用反斜杠，统一归一化再匹配
                                    _is_web = '/web/users/' in _data_dir.replace("\\", "/")
                                    _runtime_dir = WEB_PRESET_ROLES_DIR if _is_web else _custom_dir
                                    role = load_preset_role(role_id, runtime_dir=_runtime_dir, custom_dir=_custom_dir)
                                    if not role:
                                        result = {"success": False, "error": tr("tools_exec.role_not_found", role_id=role_id)}
                                    else:
                                        conv_id = self.context_manager.current_conversation_id
                                        multi_agent_state = self.sub_agent_manager.get_or_create_multi_agent_state(conv_id)
                                        # 角色内编号：显示名后缀（如 UI Operator_1），是唯一对模型/用户
                                        # 暴露的编号。采用 peek + commit：先预取构造显示名，创建成功后才
                                        # 提交计数器，失败不消耗编号，避免跳号。
                                        role_seq = multi_agent_state.peek_agent_id_for_role(role_id)
                                        # 全局 agent_id 是纯内部实现细节（任务字典 key / task_id 生成），
                                        # 不暴露给模型与用户，也不接受模型指定：自动分配对话级最小空闲正整数。
                                        agent_id = self.sub_agent_manager.next_free_agent_id(
                                            conv_id, extra_used=set(multi_agent_state.agents.keys())
                                        )
                                        # 构造显示名
                                        display_name = role.display_name(int(role_seq))
                                        # 构造多智能体版系统提示词（含动态上下文注入）
                                        workspace_path = str(getattr(self, "project_path", ""))
                                        data_dir = str(getattr(self, "data_dir", ""))
                                        # 获取当前沙箱模式
                                        sandbox_mode = ""
                                        try:
                                            if hasattr(self, "get_execution_mode_state"):
                                                state = self.get_execution_mode_state() or {}
                                                sandbox_mode = str(state.get("mode") or "")
                                        except Exception:
                                            pass
                                        system_prompt = build_multi_agent_sub_agent_prompt(
                                            role.body_prompt, display_name, workspace_path,
                                            data_dir=data_dir,
                                            sandbox_mode=sandbox_mode,
                                        )
                                        # 构造 task_message（作为 Team Leader 的任务发布）
                                        from modules.multi_agent.state import build_master_dispatch_text
                                        task_message = build_master_dispatch_text(arguments.get("task", ""))
                                        summary_text = (arguments.get("summary") or f"{role.name}作业")[:80]
                                        thinking_mode = arguments.get("thinking_mode") or role.thinking_mode or "fast"
                                        # 读取子智能体压缩阈值配置（多智能体成员长期存在，不设轮次上限，
                                        # sub_agent_max_turns 仅对传统后台子智能体生效，这里不读取）
                                        _compress_threshold = 150_000
                                        try:
                                            from modules.personalization_manager import load_personalization_config
                                            _prefs = load_personalization_config(data_dir) or {}
                                            _compress_threshold = int(_prefs.get("sub_agent_compress_threshold_tokens", 150_000))
                                        except Exception:
                                            pass
                                        # 走原行 发事件创建（避免后期重建提供重复工能重费，直接使用 multi_agent_mode=True 调用）
                                        result = self.sub_agent_manager.create_sub_agent(
                                            agent_id=agent_id,
                                            summary=summary_text,
                                            task=arguments.get("task", ""),
                                            run_in_background=False,
                                            conversation_id=conv_id,
                                            model_key=role.model_key,
                                            thinking_mode=thinking_mode,
                                            multi_agent_mode=True,
                                            role_id=role_id,
                                            display_name=display_name,
                                            system_prompt=system_prompt,
                                            task_message=task_message,
                                            compress_threshold_tokens=_compress_threshold,
                                            max_turns=None,  # 多智能体成员不设轮次上限（task.py 对 multi_agent_mode 亦强制豁免）
                                        )
                                        # 在多智能体模式下，子智能体是团队协作成员，不是传统后台任务。
                                        # run_in_background=False 避免触发后台完成通知轮询，保持主对话输入区可用。
                                        if result.get("success"):
                                            # 创建成功才提交角色内编号；失败时 peek 未提交，编号不被消耗
                                            multi_agent_state.commit_agent_id_for_role(role_id, role_seq)
                                except Exception as exc:
                                    logger.exception("[multi_agent] create_sub_agent failed")
                                    result = {"success": False, "error": str(exc)}
                        else:
                            # 读取子智能体最大轮次配置（None=默认50、0=无上限、N=N轮）
                            _max_turns = None
                            try:
                                from modules.personalization_manager import load_personalization_config
                                _prefs = load_personalization_config(str(getattr(self, "data_dir", ""))) or {}
                                _raw_max_turns = _prefs.get("sub_agent_max_turns")
                                if _raw_max_turns is not None:
                                    _max_turns = int(_raw_max_turns)
                            except Exception:
                                pass
                            result = self.sub_agent_manager.create_sub_agent(
                                agent_id=arguments.get("agent_id"),
                                summary=arguments.get("summary", ""),
                                task=arguments.get("task", ""),
                                deliverables_dir=arguments.get("deliverables_dir", ""),
                                run_in_background=arguments.get("run_in_background", False),
                                timeout_seconds=arguments.get("timeout_seconds"),
                                thinking_mode=arguments.get("thinking_mode"),
                                conversation_id=self.context_manager.current_conversation_id,
                                max_turns=_max_turns,
                            )

                            # 如果不是后台运行，阻塞等待完成
                            if not arguments.get("run_in_background", False) and result.get("success"):
                                task_id = result.get("task_id")
                                wait_result = self.sub_agent_manager.wait_for_completion(
                                    task_id=task_id,
                                    timeout_seconds=arguments.get("timeout_seconds")
                                )
                                # 合并结果：保留创建元数据，使用执行结果作为主体展示，
                                # 避免 wait_result 里的 success=False + 旧 message 导致
                                # 「create_sub_agent 失败：子智能体 X 已创建」的误导性文案。
                                creation_meta = {
                                    "agent_id": result.get("agent_id"),
                                    "task_id": result.get("task_id"),
                                    "deliverables_dir": result.get("deliverables_dir"),
                                    "run_in_background": False,
                                }
                                execution_message = (
                                    wait_result.get("message")
                                    or wait_result.get("system_message")
                                    or result.get("message")
                                )
                                result = {
                                    **result,
                                    **wait_result,
                                    **creation_meta,
                                    "message": execution_message,
                                }
                                # 阻塞式执行不需要额外插入 system 消息
                                result.pop("system_message", None)
                                # 标记已通知，避免后续轮询再插入 system 消息
                                try:
                                    task = self.sub_agent_manager.tasks.get(task_id)
                                    if isinstance(task, dict):
                                        task["notified"] = True
                                        task["updated_at"] = time.time()
                                        self.sub_agent_manager._save_state()
                                except Exception:
                                    pass

                    elif tool_name == "terminate_sub_agent":
                        if getattr(self, "multi_agent_mode", False):
                            # 多智能体模式：按显示名寻址，内部解析为全局 agent_id
                            conv_id = self.context_manager.current_conversation_id
                            state = self.sub_agent_manager.get_multi_agent_state(conv_id)
                            display_name = str(arguments.get("display_name") or "").strip()
                            inst = state.get_instance_by_display_name(display_name) if state else None
                            if not inst:
                                available = tr("tools_exec.list_sep").join(state.list_display_names()) if state else ""
                                result = {"success": False, "error": tr("tools_exec.agent_not_found", display_name=display_name, available=available or tr("tools_exec.none_placeholder"))}
                            else:
                                result = self.sub_agent_manager.terminate_sub_agent(agent_id=inst.agent_id)
                                if isinstance(result, dict):
                                    result["display_name"] = inst.display_name
                                    state.mark_status(inst.agent_id, "terminated")
                        else:
                            result = self.sub_agent_manager.terminate_sub_agent(
                                agent_id=arguments.get("agent_id")
                            )
                        # 主智能体主动终结时，tool 结果（message）已包含结论，
                        # 摘掉 system_message 避免工具循环再注入一条冗余 user 消息
                        # （且「已被手动关闭」措辞对此场景是误导）。
                        # 前端 UI 手动终止走 server/conversation.py API 直调 manager，不受影响。
                        if isinstance(result, dict):
                            result.pop("system_message", None)

                    elif tool_name == "get_sub_agent_status":
                        if getattr(self, "multi_agent_mode", False):
                            # 多智能体模式：按显示名列表查询，内部解析为全局 agent_id
                            names = arguments.get("display_names")
                            if not isinstance(names, list) or not names:
                                result = {"success": False, "error": tr("tools_exec.display_names_nonempty")}
                            else:
                                conv_id = self.context_manager.current_conversation_id
                                state = self.sub_agent_manager.get_multi_agent_state(conv_id)
                                agent_ids = []
                                not_found_names = []
                                for name in names:
                                    inst = state.get_instance_by_display_name(str(name)) if state else None
                                    if inst:
                                        agent_ids.append(inst.agent_id)
                                    else:
                                        not_found_names.append(str(name))
                                results = []
                                if agent_ids:
                                    # agent_ids 非空时 manager 侧总是返回 success=True
                                    r = self.sub_agent_manager.get_sub_agent_status(agent_ids=agent_ids)
                                    results.extend(r.get("results") or [])
                                for name in not_found_names:
                                    results.append({"found": False, "display_name": name, "error": tr("tools_exec.agent_not_exist")})
                                result = {"success": True, "results": results}
                        else:
                            result = self.sub_agent_manager.get_sub_agent_status(
                                agent_ids=arguments.get("agent_ids", [])
                            )

                    # 多智能体模式专属工具：send_message_to_sub_agent / stop_sub_agent / answer_sub_agent_question / create_custom_agent / list_agents / list_active_sub_agents
                    elif tool_name == "send_message_to_sub_agent":
                        if not getattr(self, "multi_agent_mode", False):
                            result = {"success": False, "error": tr("tools_exec.multi_agent_only")}
                        else:
                            try:
                                from modules.multi_agent.state import build_master_message_to_sub_agent
                                display_name = str(arguments.get("display_name") or "").strip()
                                message = arguments.get("message", "")
                                conv_id = self.context_manager.current_conversation_id
                                state = self.sub_agent_manager.get_multi_agent_state(conv_id)
                                if not state:
                                    result = {"success": False, "error": tr("tools_exec.multi_agent_state_not_ready")}
                                else:
                                    # 显示名寻址：模型只知道角色内编号显示名（如 UI Operator_1），
                                    # 全局 agent_id 在内部解析，不暴露给模型
                                    inst = state.get_instance_by_display_name(display_name)
                                    if not inst:
                                        available = tr("tools_exec.list_sep").join(state.list_display_names()) or tr("tools_exec.none_placeholder")
                                        result = {"success": False, "error": tr("tools_exec.agent_not_found", display_name=display_name, available=available)}
                                    else:
                                        agent_id = inst.agent_id
                                        # 构造消息文本并插入子对话
                                        text = build_master_message_to_sub_agent(message)
                                        ma_debug(
                                            "tool_send_message_to_sub_agent",
                                            agent_id=agent_id,
                                            display_name=display_name,
                                            raw_message=str(message)[:500],
                                            wrapped_message_preview=text[:500],
                                            conversation_id=conv_id,
                                        )
                                        ok = self.sub_agent_manager.inject_message_to_sub_agent(agent_id, text)
                                        if not ok:
                                            latest = self.sub_agent_manager._latest_task_for_agent(agent_id)
                                            if latest and latest.get("status") == "terminated":
                                                result = {"success": False, "error": tr("tools_exec.agent_terminated_no_message", display_name=display_name)}
                                            else:
                                                result = {"success": False, "error": tr("tools_exec.agent_not_exist_or_ended", display_name=display_name)}
                                        else:
                                            result = {"success": True, "display_name": display_name}
                                        ma_debug(
                                            "tool_send_message_to_sub_agent_result",
                                            agent_id=agent_id,
                                            conversation_id=conv_id,
                                            ok=ok,
                                            result=result,
                                        )
                            except Exception as exc:
                                logger.exception("[multi_agent] send_message_to_sub_agent failed")
                                result = {"success": False, "error": str(exc)}

                    elif tool_name == "stop_sub_agent":
                        if not getattr(self, "multi_agent_mode", False):
                            result = {"success": False, "error": tr("tools_exec.multi_agent_only")}
                        else:
                            try:
                                display_name = str(arguments.get("display_name") or "").strip()
                                conv_id = self.context_manager.current_conversation_id
                                state = self.sub_agent_manager.get_multi_agent_state(conv_id)
                                inst = state.get_instance_by_display_name(display_name) if state else None
                                if not inst:
                                    available = tr("tools_exec.list_sep").join(state.list_display_names()) if state else ""
                                    result = {"success": False, "error": tr("tools_exec.agent_not_found", display_name=display_name, available=available or tr("tools_exec.none_placeholder"))}
                                else:
                                    result = self.sub_agent_manager.stop_sub_agent(agent_id=inst.agent_id)
                                    if isinstance(result, dict):
                                        result["display_name"] = inst.display_name
                            except Exception as exc:
                                logger.exception("[multi_agent] stop_sub_agent failed")
                                result = {"success": False, "error": str(exc)}

                    elif tool_name == "answer_sub_agent_question":
                        if not getattr(self, "multi_agent_mode", False):
                            result = {"success": False, "error": tr("tools_exec.multi_agent_only")}
                        else:
                            try:
                                question_id = arguments.get("question_id", "")
                                answer = arguments.get("answer", "")
                                conv_id = self.context_manager.current_conversation_id
                                state = self.sub_agent_manager.get_multi_agent_state(conv_id)
                                ma_debug(
                                    "tool_answer_sub_agent_question",
                                    question_id=question_id,
                                    answer_preview=str(answer)[:500],
                                    conversation_id=conv_id,
                                )
                                if not state:
                                    result = {"success": False, "error": tr("tools_exec.multi_agent_state_not_ready")}
                                else:
                                    ok = state.provide_answer(question_id, answer)
                                    result = {"success": bool(ok), "question_id": question_id}
                            except Exception as exc:
                                result = {"success": False, "error": str(exc)}

                    elif tool_name == "create_custom_agent":
                        try:
                            from modules.multi_agent.role_store import RoleConfig, save_custom_role, list_roles
                            role_id = arguments.get("role_id", "").strip()
                            name = arguments.get("name", "").strip()
                            body_prompt = arguments.get("body_prompt", "").strip()
                            description = arguments.get("description", "").strip()
                            thinking_mode_arg = arguments.get("thinking_mode", "fast")
                            model_key_arg = arguments.get("model_key", "").strip() or None
                            if not role_id or not name or not body_prompt:
                                result = {"success": False, "error": tr("tools_exec.custom_agent_fields_required")}
                            else:
                                _data_dir = str(getattr(self, "data_dir", ""))
                                from modules.multi_agent.role_store import infer_custom_roles_dir
                                _custom_dir = infer_custom_roles_dir(_data_dir)
                                # Windows 下 data_dir 用反斜杠，统一归一化再匹配
                                _is_web = '/web/users/' in _data_dir.replace("\\", "/")
                                _runtime_dir = None if _is_web else _custom_dir
                                existing_ids = {r.role_id for r in list_roles(runtime_dir=_runtime_dir, custom_dir=_custom_dir)}
                                # 同 role_id 重复创建 = 覆盖式修改（仅影响之后创建的实例；运行中实例 prompt 已快照冻结）
                                role = RoleConfig(role_id=role_id, name=name, description=description, body_prompt=body_prompt, thinking_mode=thinking_mode_arg, model_key=model_key_arg, is_custom=True)
                                f = save_custom_role(role, custom_dir=_custom_dir)
                                result = {"success": True, "role_id": role_id, "name": name, "file": str(f), "overwritten": role_id in existing_ids}
                        except Exception as exc:
                            result = {"success": False, "error": str(exc)}

                    elif tool_name == "list_agents":
                        try:
                            from modules.multi_agent.role_store import list_roles, infer_custom_roles_dir
                            _data_dir = str(getattr(self, "data_dir", ""))
                            _custom_dir = infer_custom_roles_dir(_data_dir)
                            # Windows 下 data_dir 用反斜杠，统一归一化再匹配
                            _is_web = '/web/users/' in _data_dir.replace("\\", "/")
                            _runtime_dir = None if _is_web else _custom_dir
                            roles = list_roles(runtime_dir=_runtime_dir, custom_dir=_custom_dir)
                            result = {"success": True, "roles": [r.to_dict() for r in roles]}
                        except Exception as exc:
                            result = {"success": False, "error": str(exc)}

                    elif tool_name == "list_active_sub_agents":
                        try:
                            conv_id = self.context_manager.current_conversation_id
                            state = self.sub_agent_manager.get_multi_agent_state(conv_id)
                            if not state:
                                result = {"success": True, "agents": []}
                            else:
                                result = {"success": True, "agents": [a.to_dict() for a in state.list_all()]}
                        except Exception as exc:
                            result = {"success": False, "error": str(exc)}

                    elif tool_name == "trigger_easter_egg":
                        result = self.easter_egg_manager.trigger_effect(arguments.get("effect"))

                    elif tool_name == "manage_personalization":
                        logger.info("[handle_tool_call] 进入manage_personalization分支")
                        result = await self._execute_manage_personalization(arguments)
                        logger.info("[handle_tool_call] manage_personalization执行完成: result=%s", result)

                    else:
                        result = {"success": False, "error": tr("tools_exec.unknown_tool", tool_name=tool_name)}

                except Exception as e:
                    logger.error(f"工具执行失败: {tool_name} - {e}")
                    logger.exception("[handle_tool_call] 工具执行异常详情")
                    result = {"success": False, "error": tr("tools_exec.tool_exec_exception", error=str(e))}

                logger.debug("[handle_tool_call] 工具调用结束: tool_name=%s, result=%s", tool_name, result)
                return json.dumps(result, ensure_ascii=False)

    async def _execute_manage_personalization(self, arguments: Dict) -> Dict:
        """执行个性化管理操作"""
        logger.debug("[_execute_manage_personalization] 方法被调用: arguments=%s", arguments)
        action = arguments.get("action")
        logger.info("[_execute_manage_personalization] action=%s", action)
        
        if action == "read":
            logger.info("[_execute_manage_personalization] 进入read分支")
            # 读取所有配置
            try:
                config = load_personalization_config(self.data_dir)
                # 只返回可修改的字段
                readable_fields = ["self_identify", "user_name", "profession", "tone", "considerations", "theme", "communication_style", "conversation_continuity", "enabled"]
                result = {k: v for k, v in config.items() if k in readable_fields}
                # 构建详细返回信息
                field_descriptions = {
                    "enabled": tr("tools_exec.pref_enabled_label"),
                    "self_identify": tr("tools_exec.pref_self_identify", value=result.get('self_identify') or tr("tools_exec.pref_unset_paren")),
                    "user_name": tr("tools_exec.pref_user_name", value=result.get('user_name') or tr("tools_exec.pref_unset_paren")),
                    "profession": tr("tools_exec.pref_profession", value=result.get('profession') or tr("tools_exec.pref_unset_paren")),
                    "tone": tr("tools_exec.pref_tone", value=result.get('tone') or tr("tools_exec.pref_unset_paren")),
                    "considerations": tr("tools_exec.pref_considerations", value=tr("tools_exec.pref_set") if (result.get('considerations') or '').strip() else tr("tools_exec.pref_unset")),
                    "theme": tr("tools_exec.pref_theme", value=result.get('theme', 'classic')),
                    "communication_style": tr("tools_exec.pref_communication_style", value=result.get('communication_style', 'default')),
                    "conversation_continuity": tr("tools_exec.pref_conversation_continuity", value=result.get('conversation_continuity', 'medium'))
                }
                details = "\n".join([f"- {field_descriptions.get(k, k)}: {v}" for k, v in result.items()])
                logger.info("[_execute_manage_personalization] read成功")
                return {
                    "success": True,
                    "data": result,
                    "message": tr("tools_exec.pref_read_success", details=details)
                }
            except Exception as e:
                logger.error("[_execute_manage_personalization] read失败: %s", e)
                return {"success": False, "error": tr("tools_exec.pref_read_failed", error=str(e))}
        
        elif action == "update":
            logger.info("[_execute_manage_personalization] 进入update分支")
            field = arguments.get("field")
            value = arguments.get("value")
            logger.info("[_execute_manage_personalization] field=%s, value=%s", field, value)
            
            if not field:
                logger.warning("[_execute_manage_personalization] field未指定")
                return {"success": False, "error": tr("tools_exec.pref_update_needs_field")}
            
            # 验证字段是否允许修改
            logger.info("[_execute_manage_personalization] 验证字段: field=%s", field)
            allowed_fields = ["self_identify", "user_name", "profession", "tone", "considerations", "theme", "communication_style", "conversation_continuity"]
            if field not in allowed_fields:
                logger.warning("[_execute_manage_personalization] 字段不允许修改: %s not in %s", field, allowed_fields)
                return {"success": False, "error": tr("tools_exec.pref_field_not_allowed", field=field, allowed_fields=allowed_fields)}
            
            # 验证value
            validation_errors = []
            
            if field in ["self_identify", "user_name", "profession", "tone"]:
                # 字符串字段验证
                if not isinstance(value, str):
                    validation_errors.append(tr("tools_exec.pref_must_be_string", field=field))
                elif len(value) > MAX_SHORT_FIELD_LENGTH:
                    validation_errors.append(tr("tools_exec.pref_field_too_long", field=field, max_length=MAX_SHORT_FIELD_LENGTH))
            
            elif field == "considerations":
                # 注意事项文本验证
                if not isinstance(value, str):
                    validation_errors.append(tr("tools_exec.pref_considerations_must_be_string"))
                elif len(value) > MAX_CONSIDERATION_TEXT_LENGTH:
                    validation_errors.append(tr("tools_exec.pref_considerations_too_long", max_length=MAX_CONSIDERATION_TEXT_LENGTH))
            
            elif field == "theme":
                # 主题验证
                if not isinstance(value, str):
                    validation_errors.append(tr("tools_exec.pref_theme_must_be_string"))
                elif value not in ALLOWED_THEMES:
                    validation_errors.append(tr("tools_exec.pref_theme_invalid", themes=list(ALLOWED_THEMES)))
            
            elif field == "communication_style":
                # 交流风格验证
                if not isinstance(value, str):
                    validation_errors.append(tr("tools_exec.pref_comm_style_must_be_string"))
                elif value not in ALLOWED_COMMUNICATION_STYLES:
                    validation_errors.append(tr("tools_exec.pref_comm_style_invalid"))

            elif field == "conversation_continuity":
                if not isinstance(value, str):
                    validation_errors.append(tr("tools_exec.pref_continuity_must_be_string"))
                elif value not in ALLOWED_CONVERSATION_CONTINUITY:
                    validation_errors.append(tr("tools_exec.pref_continuity_invalid"))
            
            if validation_errors:
                logger.warning("[_execute_manage_personalization] 验证失败: %s", validation_errors)
                return {
                    "success": False,
                    "error": tr("tools_exec.pref_validation_failed"),
                    "validation_errors": validation_errors
                }
            
            # 加载当前配置并更新
            try:
                logger.info("[_execute_manage_personalization] 开始加载配置")
                config = load_personalization_config(self.data_dir)
                old_value = config.get(field)  # 记录旧值
                config[field] = value
                logger.info("[_execute_manage_personalization] 配置已修改: field=%s, old=%s, new=%s", field, old_value, value)
                
                # 保存配置
                save_personalization_config(self.data_dir, config)
                logger.info("[_execute_manage_personalization] 配置已保存到文件: field=%s", field)
                
                # 重新加载配置到当前终端
                self.apply_personalization_preferences(config)
                logger.info("[_execute_manage_personalization] 配置已应用到终端")
                
                # 调试日志：记录主题变更
                logger.info("[manage_personalization] 配置已更新并应用: field=%s, value=%s", field, value)
                if field == "theme":
                    logger.info("[manage_personalization] 主题已变更: old=%s, new=%s", old_value, value)
                
                # 构建返回结果
                result = {
                    "success": True,
                    "message": tr("tools_exec.pref_field_updated", field=field),
                    "updated_field": field,
                    "old_value": old_value,
                    "updated_value": value
                }
                
                # 如果是主题更新，添加主题变更标记
                if field == "theme":
                    result["theme_changed"] = True
                    result["new_theme"] = value
                    logger.info("[manage_personalization] 返回结果包含主题变更标记: theme_changed=true, new_theme=%s", value)
                
                return result
                
            except Exception as e:
                logger.error("[_execute_manage_personalization] 保存失败: %s", e)
                logger.exception("[_execute_manage_personalization] 保存异常详情")
                return {"success": False, "error": tr("tools_exec.pref_save_failed", error=str(e))}
        
        else:
            logger.warning("[_execute_manage_personalization] 未知的action: %s", action)
            return {"success": False, "error": tr("tools_exec.pref_unknown_action", action=action)}

    async def confirm_action(self, action: str, arguments: Dict) -> bool:
                """确认危险操作"""
                print(f"\n{OUTPUT_FORMATS['confirm']} 需要确认的操作:")
                print(f"  操作: {action}")
                print(f"  参数: {json.dumps(arguments, ensure_ascii=False, indent=2)}")

                response = input("\n是否继续? (y/n): ").strip().lower()
                return response == 'y'
