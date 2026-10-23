"""子智能体提示词构建。

所有 prompt 正文均从 prompts/sub_agent/ 下的文本文件加载，避免在代码中硬编码。
"""
from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from config import PROMPTS_DIR
except ImportError:
    import sys

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import PROMPTS_DIR

from modules.i18n import tr


_SUB_AGENT_PROMPTS_DIR = Path(PROMPTS_DIR) / "sub_agent"
_TEMPLATE_CACHE: dict[str, str] = {}


def _load_template(name: str) -> str:
    """从 prompts/sub_agent/<name>.txt 加载模板，带缓存。"""
    cached = _TEMPLATE_CACHE.get(name)
    if cached is not None:
        return cached

    template_path = _SUB_AGENT_PROMPTS_DIR / f"{name}.txt"
    if not template_path.exists():
        raise FileNotFoundError(tr("sa_prompts.template_missing", path=template_path))

    content = template_path.read_text(encoding="utf-8")
    _TEMPLATE_CACHE[name] = content
    return content


def _format_template(name: str, **kwargs) -> str:
    """加载模板并用 str.format 填充占位符。"""
    template = _load_template(name)
    return template.format(**kwargs)


def build_system_prompt(workspace_path: str, execution_mode: str = "") -> str:
    """构建子智能体的系统提示。

    `execution_mode` 为创建时刻的执行环境（sandbox/direct），快照写入提示词；
    后续若发生切换，由运行期通知机制补充告知（见 manager.notify_execution_mode_changed）。
    """
    from modules.execution_env_text import build_sub_agent_env_section

    from modules.inline_ui_prompt import load_inline_ui_prompt

    system_info = f"{platform.system()} {platform.release()}"
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    base_prompt = _format_template(
        "system",
        workspace_path=workspace_path,
        system_info=system_info,
        current_time=current_time,
        execution_env_section=build_sub_agent_env_section(workspace_path, execution_mode),
    )
    return f"{base_prompt.rstrip()}\n\n{load_inline_ui_prompt()}\n"
