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


class ModeMixin:
    """MainTerminalContextMixin mode 能力 mixin。"""

    # 权限模式说明（代码中存储的简要版本）
    _PERMISSION_MODE_DESC = {
        "unrestricted": "当前处于无限制模式，所有工具均可直接使用，无需额外批准。",
        "readonly": "当前处于只读模式，仅能执行不会修改工作区的读取类操作。",
        "approval": "当前处于批准模式，修改工作区的操作需要用户批准后方可执行。",
        "auto_approval": "当前处于自动审核模式，工作区内文件编辑可直接执行，高风险操作由后台审批智能体自动审核。",
    }

    _PERMISSION_MODE_LABEL = {
        "unrestricted": "无限制",
        "readonly": "只读",
        "approval": "批准",
        "auto_approval": "自动审核",
    }

    _EXECUTION_MODE_LABEL = {
        "sandbox": "沙箱",
        "direct": "完全访问权限",
    }

    _WORK_MODE_LABEL = {
        "plan": "计划",
        "ask": "询问",
        "execute": "执行",
    }

    _WORK_MODE_DESC = {
        "plan": "当前处于计划模式：只制定计划并与用户讨论，不执行任何修改。",
        "ask": "当前处于询问模式：先与用户讨论、确认细节，再开始实施。",
        "execute": "当前处于执行模式：自行梳理需求与细节，直接开工完成。",
    }

    _NETWORK_PERMISSION_LABEL = {
        "restricted": "受限",
        "full": "完全开放",
        "none": "完全禁止",
    }

    # 运行期模式（权限/执行环境/网络权限/运行模式）切换通知的 kind 与 source 映射。
    # source 需与 server/chat_flow_task_support.py 的 _VALID_SOURCES 保持一致。
    _RUNTIME_MODE_KINDS = ("permission_mode", "execution_mode", "network_permission", "work_mode")
    _RUNTIME_MODE_SOURCE = {
        "permission_mode": "permission_change",
        "execution_mode": "execution_change",
        "network_permission": "network_change",
        "work_mode": "work_mode_change",
    }
    # conversation metadata key：本对话「智能体已知晓」的运行期模式基线。
    # 只在通知真正注入对话后更新；空闲期切换不改它，从而在下一条 user 消息时
    # 通过 drift 检测一次性补发通知（天然去重：怎么切都只比较最终值）。
    _RUNTIME_MODE_BASELINE_META_KEY = "runtime_mode_baseline"

    def _build_permission_mode_message(self) -> Optional[str]:
        """根据当前权限模式构建权限说明消息（从模板读取并替换占位符）"""
        template = self.load_prompt("permission_mode")
        if not template:
            return None

        mode = self.get_permission_mode()

        # docker 运行时（终端会话为只读身份，需要补充引导）
        _session = getattr(self, "container_session", None)
        docker_runtime = bool(_session and getattr(_session, "mode", None) == "docker")

        # 根据模式生成详细规则
        if mode == "unrestricted":
            detailed_rules = (
                "- 所有命令和工具均可直接使用，无需用户批准\n"
                "- run_command：支持任意命令，包括管道、重定向、子shell等\n"
                "- 文件操作：read_file / write_file / edit_file 可直接使用；其他文件管理请通过 run_command 执行；需要 Python 时先探测并选择合适解释器"
            )
            if not docker_runtime:
                # 权限模式只管工作区内读写；宿主机沙箱的读边界与只读模式同一白名单
                detailed_rules += (
                    "\n- 可读范围：宿主机沙箱执行时同样受限（仅系统目录/工作区/路径授权可读）；"
                    "读取授权范围外的文件会被系统拒绝，需用户在「路径授权」中添加，属预期边界，不要尝试绕过"
                )
        elif mode == "readonly":
            detailed_rules = (
                "- run_command：统一在系统只读沙箱中执行，写入会被系统直接拒绝\n"
                "- 可读范围同样受限（宿主机 macOS：仅系统目录/工作区/路径授权可读；docker：受文件属主权限限制，600 等属主保护文件不可读）；读不到属预期边界，不要尝试绕过\n"
                "- 文件操作：仅允许 read_file、view_image、view_video 等读取类工具\n"
                "- 禁止：write_file、edit_file 及会修改工作区的命令操作\n"
                "- 终端会话（terminal_*）：可自由创建与输入，但终端以只读身份运行（docker 非特权 uid / 宿主机只读沙箱 profile），写入会被系统拒绝，属预期边界"
            )
        elif mode == "approval":
            detailed_rules = (
                "- run_command：先在系统只读沙箱执行（可读范围同样受限）；仅当出现权限拒绝时才触发审批\n"
                "- 审批通过后：仅当前这一次命令会以可写沙箱重试；审批只授予工作区内写权限，不放大读取范围\n"
                "- 读取授权范围外的路径：审批无法放行，需用户在「路径授权」中添加\n"
                "- 需要用户批准：write_file、edit_file、save_webpage 及其他会修改工作区的操作\n"
                "- 被拒绝或超时：本次操作不会执行写入\n"
                "- 执行环境已锁定为沙箱（完全访问仅在无限制模式下可选）\n"
                "- 终端会话（terminal_*）可直接创建与输入（无需批准），但以只读身份运行（docker 非特权 uid / 宿主机只读沙箱 profile）：终端里的写入会被系统拒绝，需要写入的命令请用 run_command（审批通过后以可写身份重跑），属预期边界"
            )
        elif mode == "auto_approval":
            detailed_rules = (
                "- write_file / edit_file：若路径在当前工作区内，直接执行；工作区外路径会进入审批流程\n"
                "- run_command：先在系统只读沙箱执行（可读范围同样受限）；仅当出现权限拒绝时才触发自动审批\n"
                "- 审批只授予工作区内写权限，不放大读取范围（读越界需用户在「路径授权」中添加）\n"
                "- 自动审批由后台审批智能体执行，默认只判断危险性与越权风险，不判断任务必要性\n"
                "- 自动审批拒绝：本次工具调用会返回“拒绝+理由”，主循环继续；人工拒绝可随时接管\n"
                "- 可随时人工接管：用户在审批面板点击同意/拒绝/切换无限制后，自动审批会立即停止并以人工决策为准\n"
                "- 执行环境已锁定为沙箱（完全访问仅在无限制模式下可选）\n"
                "- 终端会话（terminal_*）以只读身份运行（docker 非特权 uid / 宿主机只读沙箱 profile）：终端里的写入会被系统拒绝，需要写入的命令请用 run_command（审批通过后以可写身份重跑），属预期边界"
            )
        else:
            detailed_rules = ""

        return template.format(
            permission_mode=mode,
            permission_mode_label=self._PERMISSION_MODE_LABEL.get(mode, mode),
            mode_description=self._PERMISSION_MODE_DESC.get(mode, ""),
            detailed_rules=detailed_rules,
        )

    def _build_work_mode_rules(self, mode: str) -> str:
        """三档运行模式的行为规则（系统提示词与切换通知共用，保证两处一致）。"""
        if mode == "plan":
            return (
                "- 本模式下你只制定计划、与用户讨论方案，**禁止执行任何修改**：不要调用写工具，也不要运行任何会修改文件或系统状态的命令\n"
                "- 权限模式已被锁定为只读、执行环境已被锁定为沙箱，修改型操作会被系统直接拒绝，不要尝试绕过\n"
                "- 唯一例外：可以使用 write_file / edit_file 在工作区 `.astrion/plan/` 目录下创建和修改计划文档（.md），该目录之外的写入依然被拒绝\n"
                "- 工作方式：充分调研（读代码、搜索、与用户讨论）→ 把计划写入 `.astrion/plan/` 下的计划文档（包含：目标理解、方案与取舍、分步实施步骤、涉及的关键文件、验证方式）→ 调用 submit_plan 提请用户批准\n"
                "- 计划保持精炼可读，突出关键决策点；过长的计划会降低用户批准意愿\n"
                "- 你的一轮输出只应以两种方式结束：向用户提问/讨论，或调用 submit_plan 提交计划。不要不了了之\n"
                "- 用户批准后系统会自动切换到执行模式；用户拒绝时会附带意见，你应据此修订计划文档后重新提交"
            )
        if mode == "ask":
            return (
                "- 先讨论，后开工：收到任务后先快速了解必要背景，然后把你的理解、拟定的方案、需要用户拍板的问题**直接写在回复里**与用户讨论\n"
                "- **禁止使用 ask_user 工具**：本模式的讨论是开放式的多轮交流，直接在回复正文中输出讨论内容即可\n"
                "- 与用户确认清楚关键细节之前，不要开始正式实施（不要改文件、不要跑修改型命令）\n"
                "- 用户确认后（包括用户直接说「开始」「动手」「go」等），视为细节已拍板，此后自主完成剩余工作，不再逐项征求同意"
            )
        if mode == "execute":
            return (
                "- 直接开工：用户的要求即最终需求，自行梳理、制定计划并立即着手实施，不要停下来向用户复述确认\n"
                "- 自行补全用户未说明的细节：按最合理的方式决策并继续推进，不要就设计取舍、命名、路径等细节向用户提问\n"
                "- 仅当出现缺少你无法自行获取的信息（如密钥、账号）或任何方案都有重大风险的硬阻塞时，才允许向用户提问\n"
                "- 完成后自行验证（构建/测试/检查），再在最终回复中汇报结果，并列出你做出的关键假设以便用户纠正"
            )
        return ""

    def _build_work_mode_message(self) -> Optional[str]:
        """根据当前运行模式构建运行模式说明消息（从模板读取并替换占位符）。"""
        template = self.load_prompt("work_mode")
        if not template:
            return None
        mode = "plan"
        try:
            if hasattr(self, "get_work_mode"):
                mode = self.get_work_mode()
        except Exception:
            pass
        return template.format(
            work_mode=mode,
            work_mode_label=self._WORK_MODE_LABEL.get(mode, mode),
            mode_description=self._WORK_MODE_DESC.get(mode, ""),
            detailed_rules=self._build_work_mode_rules(mode),
        )

    def _build_execution_mode_message(self) -> Optional[str]:
        """根据当前执行环境模式构建提示消息。"""
        # 仅宿主机模式注入；Docker 模式不需要该提示。
        try:
            if not getattr(self, "_is_host_mode", lambda: False)():
                return None
        except Exception:
            return None
        state = {}
        if hasattr(self, "get_execution_mode_state"):
            try:
                state = self.get_execution_mode_state() or {}
            except Exception:
                state = {}
        mode = str(state.get("mode") or "sandbox").strip().lower()
        mode_label = self._EXECUTION_MODE_LABEL.get(mode, mode)

        import platform
        if platform.system() == "Windows":
            template = self.load_prompt("execution_mode/windows")
            if not template:
                return None
            return template.format(
                execution_mode=mode,
                execution_mode_label=mode_label,
                environment_rules=self._build_windows_environment_rules(mode),
                rules=self._build_windows_mode_rules(mode),
                network_rules=self._build_network_rules_line() if mode == "sandbox" else "",
                switch_invariant=(
                    "注意：执行环境可能在对话过程中被用户切换。切换时你会收到一条新的环境说明，"
                    "与本文不一致时，永远以最新的环境说明为准。"
                ),
            )

        template = self.load_prompt("execution_mode/macos")
        if not template:
            return None
        if mode == "sandbox":
            rules = (
                "- 所有命令默认在系统 OS 沙箱中执行\n"
                "- 若遇到权限问题：这是用户刻意设置导致的结果，证明用户在当前状况下不允许你执行此命令。不要尝试绕过，你应立刻询问用户并解释原因，请求用户更换执行环境（如切换为“完全访问权限”）"
            )
        else:
            rules = (
                "- 当前为宿主机直接执行模式（完全访问权限）\n"
                "- 仅在必须时执行高权限操作，保持最小化命令范围\n"
                "- 涉及删除/覆盖/系统级变更前，先说明风险再执行"
            )
        return template.format(
            execution_mode=mode,
            execution_mode_label=mode_label,
            rules=rules,
            network_rules=self._build_network_rules_line() if mode == "sandbox" else "",
        )

    def _build_network_rules_line(self) -> str:
        """网络档位说明行（两平台共用）。"""
        net = getattr(self, "host_network_permission", "restricted")
        if net == "restricted":
            return "- 网络：受限（仅允许 localhost，外部网络不可达）"
        if net == "full":
            return "- 网络：完全开放"
        if net == "none":
            return "- 网络：完全禁止"
        return ""

    def _workspace_win_path(self) -> str:
        path = str(getattr(self, "project_path", "") or "").strip()
        if not path:
            path = str(getattr(getattr(self, "context_manager", None), "project_path", "") or "").strip()
        return path

    def _workspace_wsl_path(self) -> str:
        try:
            from modules.host_sandbox_runner import _win_path_to_wsl
            return _win_path_to_wsl(Path(self._workspace_win_path()).resolve())
        except Exception:
            return ""

    def _build_windows_environment_rules(self, mode: str) -> str:
        """Windows 执行环境说明（系统提示词与切换通知共用，保证两处一致）。"""
        ws_win = self._workspace_win_path() or "<工作区>"
        if mode != "sandbox":
            return (
                "### 环境与路径（Windows 原生）\n"
                "- 命令解释器：Windows cmd（可用 dir、type、findstr 及系统已安装的所有程序）\n"
                f"- 当前工作区：{ws_win}\n"
                "- 路径使用 Windows 风格，注意引号内反斜杠的转义"
            )
        ws_wsl = self._workspace_wsl_path() or "<工作区 WSL 路径>"
        return (
            "### 环境与路径（WSL2 Linux 沙箱）\n"
            "- 命令解释器：Linux bash（例如可用 ls、cat、grep、find、git、python3、curl 等常见 Linux 工具）\n"
            f"- 当前工作区：{ws_win}（Windows 视角）= {ws_wsl}（沙箱内视角）\n"
            f"- 命令的工作目录已默认落在 {ws_wsl}，操作工作区内文件请直接使用相对路径\n"
            "- 沙箱内只能访问当前工作区：其他 Windows 路径（如 D:\\tools\\a.txt）未挂载进沙箱、根本不存在，需要操作时请把文件复制进工作区，或请用户调整工作区\n"
            "- 【禁止】Windows 程序在沙箱内不存在：cmd、powershell、bat 脚本、.exe、Windows 版 python/node 均无法运行\n"
            "- 【禁止】不要写 Windows 风格路径，bash 会把反斜杠当作转义符\n"
            "\n"
            "### 文件权限\n"
            "- 工作区内：可读可写\n"
            "- 工作区外：数据在沙箱内不存在（Windows 各盘与其余目录均未挂载），访问报 No such file or directory；仅通往工作区的空壳目录链可见\n"
            "- Linux 系统目录（/bin /usr /lib /etc 等，沙箱自带工具链）：只读\n"
            "- /tmp 与 HOME 目录可写（npm/pip 等工具的缓存可正常使用）"
        )

    def _build_windows_mode_rules(self, mode: str) -> str:
        """Windows 当前规则段（与 mac 版语义对齐）。"""
        if mode == "sandbox":
            return (
                "- 所有命令默认在 WSL2 Linux 沙箱中执行\n"
                "- 命令报 Read-only file system / Permission denied，或访问工作区外路径报 No such file or directory：说明操作超出沙箱授权边界，这是用户刻意设置的。不要尝试绕过（换路径、提权等），应向用户说明并请求调整权限或切换执行环境\n"
                "- 命令报 command not found：先检查是否误用了 Windows 程序；Linux 工具缺失时可建议用户安装\n"
                "- 需要 Windows 原生工具链的任务（如为 Windows 版 node 安装依赖）：明确告知用户需切换到“完全访问权限”执行"
            )
        return (
            "- 当前为宿主机直接执行模式（完全访问权限）\n"
            "- 仅在必须时执行高权限操作，保持最小化命令范围\n"
            "- 涉及删除/覆盖/系统级变更前，先说明风险再执行\n"
            "- 操作尽量限制在当前工作区内"
        )

    def _build_execution_mode_switch_notice(self, mode: str) -> str:
        """执行环境切换通知文本。mac 为一句话；Windows 为完整环境说明（与系统提示词共用构建）。"""
        label = self._EXECUTION_MODE_LABEL.get(mode, mode)
        import platform
        if platform.system() != "Windows":
            return f"执行环境被用户修改为 {label}"
        ws_win = self._workspace_win_path() or "<工作区>"
        if mode == "sandbox":
            ws_wsl = self._workspace_wsl_path() or "<工作区 WSL 路径>"
            network_line = self._build_network_rules_line().lstrip("- ").strip()
            network_part = f"5. {network_line}\n" if network_line else ""
            final_no = 6 if network_line else 5
            return (
                "【执行环境已切换】用户已将执行环境切换为：沙箱（WSL2 Linux）。\n"
                "\n"
                "你后续的终端命令将不再在 Windows 中执行，而是在 WSL2 Linux 沙箱中以 bash 执行。请立即调整命令写法：\n"
                "\n"
                "1. 解释器变为 Linux bash：使用 ls、cat、grep 等 Linux 命令；cmd、powershell、.exe 等 Windows 程序从此刻起不可用\n"
                f"2. 路径必须转换：当前工作区为 {ws_wsl}（即 Windows 的 {ws_win}），工作目录已默认落在此处，工作区内操作请用相对路径；沙箱内只能访问工作区，其他 Windows 路径均未挂载、无法读写\n"
                "3. 禁止再写 Windows 风格路径，反斜杠会被 bash 当作转义符\n"
                "4. 写权限：仅工作区、/tmp、HOME 可写；Linux 系统目录只读；其余路径在沙箱内不存在\n"
                f"{network_part}"
                f"{final_no}. 遇到 Read-only file system / Permission denied / 区外路径 No such file or directory 不要绕过，向用户说明并请求调整\n"
                "\n"
                "此前的命令如果是按 Windows 环境编写的，请按上述规则改写后重新执行。"
            )
        return (
            "【执行环境已切换】用户已将执行环境切换为：完全访问权限（Windows 原生）。\n"
            "\n"
            "你后续的终端命令将直接在 Windows 宿主机上执行，拥有用户的完整权限。请立即调整命令写法：\n"
            "\n"
            "1. 解释器为 Windows cmd：可使用 dir、type、findstr 及系统已安装的所有程序；未单独安装的 Linux 命令（ls、grep 等）不可用\n"
            f"2. 路径恢复 Windows 风格：当前工作区为 {ws_win}，不要再使用 /mnt/ 形式的 WSL 路径\n"
            "3. 你不再处于沙箱边界内：请保持最小化命令范围，操作尽量限制在工作区内；涉及删除/覆盖/系统级变更前，先向用户说明风险\n"
            "\n"
            "此前的命令如果是按 Linux 沙箱环境编写的，请按上述规则改写后重新执行。"
        )

    # ---- 运行期模式基线（权限/执行环境/网络权限的「智能体已知晓」状态） ----

    def _current_runtime_modes(self) -> Dict[str, str]:
        """当前实际生效的三种运行期模式。"""
        modes: Dict[str, str] = {}
        try:
            if hasattr(self, "get_permission_mode"):
                modes["permission_mode"] = str(self.get_permission_mode() or "")
        except Exception:
            pass
        try:
            if hasattr(self, "get_execution_mode"):
                modes["execution_mode"] = str(self.get_execution_mode() or "")
        except Exception:
            pass
        try:
            if hasattr(self, "get_network_permission"):
                modes["network_permission"] = str(self.get_network_permission() or "")
        except Exception:
            pass
        try:
            if hasattr(self, "get_work_mode"):
                modes["work_mode"] = str(self.get_work_mode() or "")
        except Exception:
            pass
        return modes

    def get_runtime_mode_baseline(self) -> Dict[str, str]:
        """读取本对话的运行期模式基线（conversation metadata）。"""
        cm = getattr(self, "context_manager", None)
        meta = getattr(cm, "conversation_metadata", None) if cm else None
        raw = meta.get(self._RUNTIME_MODE_BASELINE_META_KEY) if isinstance(meta, dict) else None
        return dict(raw) if isinstance(raw, dict) else {}

    def update_runtime_mode_baseline(self, updates: Dict[str, str]) -> None:
        """通知真正注入对话后更新基线。只允许在通知注入点调用。"""
        if not isinstance(updates, dict) or not updates:
            return
        baseline = self.get_runtime_mode_baseline()
        baseline.update({k: str(v) for k, v in updates.items() if v})
        if hasattr(self, "_persist_runtime_mode_metadata"):
            try:
                self._persist_runtime_mode_metadata({self._RUNTIME_MODE_BASELINE_META_KEY: baseline})
            except Exception:
                pass

    def collect_runtime_mode_drift(self) -> List[Dict[str, str]]:
        """比较当前实际模式与本对话基线，返回需要补发通知的差异列表。

        - 基线缺失（新对话/历史对话）：静默初始化为当前模式，返回空列表（不通知）。
          新对话首轮消息的冻结系统提示词本身就是当前模式，无需额外通知。
        - 返回项结构：{"kind": ..., "mode": ..., "source": ...}
        """
        current = self._current_runtime_modes()
        baseline = self.get_runtime_mode_baseline()
        if not baseline:
            self.update_runtime_mode_baseline(current)
            return []
        drift: List[Dict[str, str]] = []
        for kind in self._RUNTIME_MODE_KINDS:
            cur = current.get(kind)
            if not cur:
                continue
            if baseline.get(kind) != cur:
                drift.append({
                    "kind": kind,
                    "mode": cur,
                    "source": self._RUNTIME_MODE_SOURCE.get(kind, "notify"),
                })
        return drift

    def build_runtime_mode_switch_notice(self, kind: str, mode: str) -> str:
        """构建运行期模式切换通知文本（运行期注入与空闲期补注共用，保证两处一致）。"""
        if kind == "permission_mode":
            label = self._PERMISSION_MODE_LABEL.get(mode, mode)
            return f"权限模式被用户修改为 {label}"
        if kind == "network_permission":
            label = self._NETWORK_PERMISSION_LABEL.get(mode, mode)
            return f"网络权限被用户修改为 {label}"
        if kind == "execution_mode":
            if hasattr(self, "_build_execution_mode_switch_notice"):
                return self._build_execution_mode_switch_notice(mode)
            label = self._EXECUTION_MODE_LABEL.get(mode, mode)
            return f"执行环境被用户修改为 {label}"
        if kind == "work_mode":
            # 运行模式的行为规则差异大，切换通知必须携带完整新模式规则
            # （冻结的系统提示词仍是旧模式，以此为准覆盖）。
            label = self._WORK_MODE_LABEL.get(mode, mode)
            rules = self._build_work_mode_rules(mode)
            header = f"运行模式被用户修改为 {label}"
            if rules:
                return f"{header}。与之前系统提示词中的运行模式说明不一致时，以本次为准。\n\n### 当前规则\n{rules}"
            return header
        return f"运行模式被用户修改为 {mode}"

    def _get_or_init_frozen_mode_prompt(self, key: str, builder) -> Optional[str]:
            return self._get_or_init_frozen_prompt(key, builder)

    def _get_or_init_frozen_prompt(self, key: str, builder) -> Optional[str]:
            cm = getattr(self, "context_manager", None)
            meta = getattr(cm, "conversation_metadata", {}) if cm else {}
            cached = meta.get(key) if isinstance(meta, dict) else None
            if isinstance(cached, str) and cached:
                return cached

            built = builder() or ""
            conv_id = getattr(cm, "current_conversation_id", None) if cm else None
            if cm and conv_id:
                try:
                    target_manager = (
                        cm._get_conversation_manager_for_id(conv_id)
                        if hasattr(cm, "_get_conversation_manager_for_id")
                        else cm.conversation_manager
                    )
                    target_manager.update_conversation_metadata(conv_id, {key: built})
                    if isinstance(cm.conversation_metadata, dict):
                        cm.conversation_metadata[key] = built
                except Exception:
                    pass
            return built
