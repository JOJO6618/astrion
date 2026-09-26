"""搜索相关配置。"""

import os

# 选择 Tavily 使用哪个环境变量中的密钥。
# 默认保持兼容：仍使用 AGENT_TAVILY_API_KEY。
# 你可以改成例如：AGENT_TAVILY_API_KEY_2 / AGENT_TAVILY_API_KEY_BACKUP
TAVILY_API_KEY_ENV_NAME = "AGENT_TAVILY_API_KEY"

# 实际生效的 Tavily 密钥
TAVILY_API_KEY = os.environ.get(TAVILY_API_KEY_ENV_NAME, "")

# 其余搜索服务商的环境变量密钥（设置页 UI 配置优先，这里是兜底）
BOCHA_API_KEY = os.environ.get("AGENT_BOCHA_API_KEY", "")
EXA_API_KEY = os.environ.get("AGENT_EXA_API_KEY", "")
PARALLEL_API_KEY = os.environ.get("AGENT_PARALLEL_API_KEY", "")
# SearXNG 无需密钥，用实例地址
SEARXNG_BASE_URL = os.environ.get("AGENT_SEARXNG_BASE_URL", "")
# Jina Reader（网页提取降级链；无 key 也可匿名限速使用，key 提高额度）
JINA_API_KEY = os.environ.get("AGENT_JINA_API_KEY", "")

__all__ = [
    "TAVILY_API_KEY_ENV_NAME",
    "TAVILY_API_KEY",
    "BOCHA_API_KEY",
    "EXA_API_KEY",
    "PARALLEL_API_KEY",
    "SEARXNG_BASE_URL",
    "JINA_API_KEY",
]
