"""共享 show_ui 与 show_html 创作契约，保留 HTML/JavaScript 字面量。"""
from pathlib import Path

from config import PROMPTS_DIR


def load_inline_ui_prompt() -> str:
    """按固定顺序加载两份独立规则，由调用方按对话或子智能体创建时冻结。"""
    return "\n\n".join(
        (Path(PROMPTS_DIR) / name).read_text(encoding="utf-8").strip()
        for name in ("show_ui.txt", "show_html.txt")
    )
