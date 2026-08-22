"""搜索相关配置。"""

import os

# 选择 Tavily 使用哪个环境变量中的密钥。
# 默认保持兼容：仍使用 AGENT_TAVILY_API_KEY。
# 你可以改成例如：AGENT_TAVILY_API_KEY_2 / AGENT_TAVILY_API_KEY_BACKUP
TAVILY_API_KEY_ENV_NAME = "AGENT_TAVILY_API_KEY"

# 实际生效的 Tavily 密钥
TAVILY_API_KEY = os.environ.get(TAVILY_API_KEY_ENV_NAME, "")

__all__ = [
    "TAVILY_API_KEY_ENV_NAME",
    "TAVILY_API_KEY",
]

