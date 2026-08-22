"""子智能体执行环境说明文本（精简版，三时点共用）。

主智能体的完整版见 core/main_terminal_parts/context/mode.py
（_build_windows_environment_rules 等）；本模块面向子智能体场景：

- 创建时：注入系统提示词的环境段（build_sub_agent_env_section）
- 切换时：注入运行中子智能体的上下文通知（build_sub_agent_mode_switch_notice）
- 恢复时：任务从磁盘恢复后的环境告知（build_sub_agent_restore_notice）

注意：子智能体没有切换执行环境的入口，因此文本中不要出现
「请求用户切换执行环境」之类的引导，改为「在报告中说明」。
"""
from __future__ import annotations

import platform


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _normalize_mode(execution_mode: str) -> str:
    return "direct" if str(execution_mode or "").strip().lower() == "direct" else "sandbox"


def build_sub_agent_env_section(workspace_path: str, execution_mode: str) -> str:
    """创建/恢复时注入系统提示词的执行环境段。

    execution_mode: "sandbox" / "direct"（其他值按 sandbox 处理）。
    workspace_path 为宿主机视角路径（Windows 下为反斜杠路径）。
    """
    mode = _normalize_mode(execution_mode)
    ws = str(workspace_path or "").strip() or "<工作区>"

    if _is_windows():
        if mode == "direct":
            return (
                "## 执行环境\n\n"
                "- 运行环境：Windows 宿主机，直接执行（完全访问权限）\n"
                "- 命令解释器：Windows cmd（可用 dir、type、findstr 及系统已安装的程序；\n"
                "  ls/grep/find/sed 等 Linux 命令与 bash 语法不可用）\n"
                f"- 工作区路径：{ws}（Windows 风格，引号内注意反斜杠转义）\n"
                "- 仅在必须时执行高权限操作；涉及删除/覆盖/系统级变更前，在报告中说明风险\n"
            )
        return (
            "## 执行环境\n\n"
            "- 运行环境：Windows 宿主机，WSL2 Linux 沙箱执行\n"
            "- 命令解释器：Linux bash（可用 ls、cat、grep、find、python3 等 Linux 工具；\n"
            "  cmd、powershell、.exe 等 Windows 程序不可用）\n"
            f"- 工作区路径：{ws}（Windows 视角）；命令的工作目录已默认落在沙箱内对应 Linux 路径，\n"
            "  工作区内操作请直接使用相对路径\n"
            "- 引用其他 Windows 路径必须转换：盘符小写、反斜杠变正斜杠，\n"
            "  例如 D:\\tools\\a.txt → /mnt/d/tools/a.txt；bash 会把反斜杠当作转义符\n"
            "- 工作区外全部只读（写入报 Read-only file system），这是刻意设置的边界，\n"
            "  不要尝试绕过；无法满足时在报告中说明\n"
        )

    # macOS / Linux：沙箱与直接执行同为 POSIX shell，仅权限边界不同
    if mode == "direct":
        return (
            "## 执行环境\n\n"
            "- 运行环境：宿主机直接执行（完全访问权限）\n"
            "- 命令解释器：POSIX shell（可用 ls、cat、grep、find 等）\n"
            "- 仅在必须时执行高权限操作；涉及删除/覆盖/系统级变更前，在报告中说明风险\n"
        )
    return (
        "## 执行环境\n\n"
        "- 运行环境：宿主机，系统 OS 沙箱执行\n"
        "- 命令解释器：POSIX shell（可用 ls、cat、grep、find 等）\n"
        "- 若操作受系统权限限制：这是用户刻意设置的边界。先提供安全替代方案；\n"
        "  仍无法满足时在报告中说明，不要尝试绕过\n"
    )


def build_sub_agent_mode_switch_notice(execution_mode: str) -> str:
    """执行环境切换通知正文（运行中以 user 消息注入，纯上下文，不触发新一轮工作）。"""
    mode = _normalize_mode(execution_mode)
    label = "完全访问权限" if mode == "direct" else "沙箱"

    if _is_windows():
        if mode == "direct":
            detail = (
                "你后续的终端命令将改为在 Windows cmd 中执行，请立即调整命令写法：\n"
                "- 使用 dir、type、findstr 等 cmd 命令；ls/grep/find 等 Linux 命令与 bash 语法不再可用\n"
                "- 路径改用 Windows 风格（如 E:\\proj\\file.txt）\n"
                "- 此前按 Linux 环境得到的结论（路径、工具可用性）可能不再适用"
            )
        else:
            detail = (
                "你后续的终端命令将改为在 WSL2 Linux 沙箱中以 bash 执行，请立即调整命令写法：\n"
                "- 使用 ls、cat、grep 等 Linux 命令；cmd、powershell、.exe 等 Windows 程序不再可用\n"
                "- 工作区内操作请用相对路径；其他 Windows 路径按 D:\\a\\b → /mnt/d/a/b 转换\n"
                "- 工作区外只读，写入报 Read-only file system 属预期边界，不要绕过\n"
                "- 此前按 Windows 环境得到的结论（路径、工具可用性）可能不再适用"
            )
        return f"执行环境已被切换为：{label}。\n{detail}"

    return f"执行环境已被切换为：{label}（命令语言不变，仍为 POSIX shell，注意权限边界变化）。"


def build_sub_agent_restore_notice(workspace_path: str, execution_mode: str) -> str:
    """任务从磁盘恢复后的环境告知正文（恢复时以 user 消息注入，不触发新一轮工作）。"""
    section = build_sub_agent_env_section(workspace_path, execution_mode)
    return (
        "任务已从持久化状态恢复运行。以下是你当前的实际执行环境"
        "（可能与任务创建时不同，以本说明为准）：\n\n" + section
    )
