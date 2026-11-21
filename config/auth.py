"""认证与后台账户配置。

配置来源（优先级从高到低）：
1. 环境变量（os.environ，由 config/__init__.py 从 settings.json / .env 注入）
2. 默认值
"""

import os


def _get(name: str, default: str = "") -> str:
    """从环境变量获取配置。"""
    return str(os.environ.get(name, "") or "").strip() or default


# 管理员二级密码（可选）。
ADMIN_SECONDARY_PASSWORD_HASH = _get("AGENT_CFG_ADMIN_SECONDARY_PASSWORD_HASH") or _get("ADMIN_SECONDARY_PASSWORD_HASH", "")
ADMIN_SECONDARY_PASSWORD = _get("ADMIN_SECONDARY_PASSWORD", "")

# 二级密码会话有效期（秒）。默认 30 分钟。
ADMIN_SECONDARY_TTL_SECONDS = int(_get("AGENT_ADMIN_SECONDARY_TTL_SECONDS", "1800") or 1800)

__all__ = [
    "ADMIN_SECONDARY_PASSWORD_HASH",
    "ADMIN_SECONDARY_PASSWORD",
    "ADMIN_SECONDARY_TTL_SECONDS",
]
