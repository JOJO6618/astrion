"""存储空间统计的分类定义（唯一来源）。

只描述「数据根内」的路径前缀；工作区项目目录里的 ``.astrion/`` 不参与统计。
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

# 展示顺序即此顺序（前端按 id 取文案与颜色）
CATEGORY_IDS: Tuple[str, ...] = (
    "backups",
    "logs",
    "mcp_profiles",
    "agent_conversations",
    "media",
    "sub_agents",
    "chat",
    "other",
)

# 数据根内的目录前缀规则；同一路径命中多条时取最长前缀
# （``conversations/media_store`` 必须先于 ``conversations`` 命中）
DIR_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("media", ("conversations", "media_store")),
    ("backups", ("save",)),
    ("mcp_profiles", ("mcp_profiles",)),
    ("agent_conversations", ("conversations",)),
    ("sub_agents", ("sub_agent_tasks",)),
    ("chat", ("chat",)),
)

# 数据根内的单文件规则（优先于目录规则）
FILE_RULES: Dict[str, str] = {
    "sub_agent_runtime.json": "sub_agents",
    "sub_agents.json": "sub_agents",
}

# 对话备份目录命名前缀：save/<conv_id>
BACKUP_PREFIX = "conv_"


def classify(parts: Sequence[str]) -> str:
    """按数据根内的相对路径（目录名 + 文件名）返回类别 id，未命中归 other。"""
    if not parts:
        return "other"
    if len(parts) == 1:
        hit = FILE_RULES.get(parts[0])
        if hit:
            return hit
    best: Optional[str] = None
    best_len = 0
    for category, prefix in DIR_RULES:
        size = len(prefix)
        if size <= best_len or size > len(parts):
            continue
        if tuple(parts[:size]) == prefix:
            best, best_len = category, size
    return best or "other"


__all__ = ["CATEGORY_IDS", "DIR_RULES", "FILE_RULES", "BACKUP_PREFIX", "classify"]
