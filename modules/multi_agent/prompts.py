"""多智能体模式的系统提示词。

所有 prompt 正文均从 prompts/multi_agent/ 下的文本文件加载，避免在代码中硬编码。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

try:
    from config import PROMPTS_DIR
except ImportError:
    import sys

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import PROMPTS_DIR

from modules.i18n import tr


_MULTI_AGENT_PROMPTS_DIR = Path(PROMPTS_DIR) / "multi_agent"
_TEMPLATE_CACHE: dict[str, str] = {}


def _load_template(name: str) -> str:
    """从 prompts/multi_agent/<name>.txt 加载模板，带缓存。"""
    cached = _TEMPLATE_CACHE.get(name)
    if cached is not None:
        return cached

    template_path = _MULTI_AGENT_PROMPTS_DIR / f"{name}.txt"
    if not template_path.exists():
        raise FileNotFoundError(tr("ma_prompts.template_missing", path=template_path))

    content = template_path.read_text(encoding="utf-8")
    _TEMPLATE_CACHE[name] = content
    return content


def _format_template(name: str, **kwargs) -> str:
    """加载模板并用 str.format 填充占位符。"""
    template = _load_template(name)
    return template.format(**kwargs)


# 为兼容外部仍可通过常量访问正文，但值来自文件；首次访问时加载。
def __getattr__(name: str) -> str:
    if name == "MULTI_AGENT_MASTER_PROMPT_BODY":
        return _load_template("master")
    if name == "MULTI_AGENT_SUB_AGENT_PROMPT_BODY":
        return _load_template("sub_agent")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_multi_agent_master_prompt(workspace_path: str, base: str = "") -> str:
    """构造主智能体（Team Leader）的系统提示词。

    `base` 一般为现有 MainTerminal 的 base 提示词（环境/工具概览等），
    我们在末尾追加多智能体模式专属正文。
    """
    body = _load_template("master")
    if base and base.strip():
        return f"{base.rstrip()}\n\n{body}\n"
    return f"{body}\n"


def build_multi_agent_sub_agent_prompt(
    role_body: str,
    display_name: str,
    workspace_path: str,
    *,
    data_dir: str = "",
    sandbox_mode: str = "",
) -> str:
    """构造子智能体的系统提示词。

    `role_body` 为该角色 Markdown 文件 frontmatter 之后的自定义 prompt。
    `display_name` 为该实例的显示名（如 `UI Operator_1`）。
    `workspace_path` 为工作区路径，用于注入动态上下文。
    `data_dir` 为数据目录路径，用于推断 private skills 目录。
    `sandbox_mode` 为沙箱模式（sandbox/direct/host/docker）。

    所有动态信息在子智能体创建那一刻快照写入，后续冻结不更新。
    """
    # 角色专属 prompt（模板正文）
    role_prompt = _format_template("sub_agent", display_name=display_name, role_body=role_body.strip())

    # 动态上下文（AGENTS.md / 执行环境 / 工作区信息 / skills / 项目记忆等）
    try:
        from modules.multi_agent.sub_agent_context import build_sub_agent_dynamic_context
        dynamic_context = build_sub_agent_dynamic_context(
            workspace_path,
            data_dir=data_dir,
            sandbox_mode=sandbox_mode,
        )
    except Exception:
        dynamic_context = ""

    if dynamic_context:
        return f"{role_prompt.rstrip()}\n\n---\n\n{dynamic_context}\n"
    return f"{role_prompt}\n"


def build_available_agents_prompt() -> str:
    """构造「可用的子智能体角色」动态 prompt。

    列出当前可创建的所有角色名称与说明，供 Team Leader 参考。
    若没有任何可用角色，返回空字符串。
    """
    try:
        from modules.multi_agent.role_store import list_roles

        roles = list_roles()
    except Exception:
        return ""

    if not roles:
        return ""

    lines: List[str] = []
    for role in roles:
        name = getattr(role, "name", "") or ""
        description = getattr(role, "description", "") or ""
        role_id = getattr(role, "role_id", "") or ""
        if description:
            lines.append(f"- {name}（role_id: {role_id}）：{description}")
        else:
            lines.append(f"- {name}（role_id: {role_id}）")

    if not lines:
        return ""

    return _format_template("available_agents", agents_list="\n".join(lines))
