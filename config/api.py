"""API 和外部服务配置。

模型的 API 端点 / 密钥 / 模型 ID 现已统一由 ``config/custom_models.json``
（经 ``config/model_profiles.py`` 解析为模型档案）描述，并在运行时通过
``APIClient.apply_profile`` 注入。旧的 ``AGENT_API_BASE_URL`` /
``AGENT_API_KEY`` / ``AGENT_MODEL_ID``（以及 THINKING / TITLE 三件套）属于早期
把模型逻辑硬编码在程序里的残留，已移除。没有任何可用模型时，
``model_profiles.get_default_model_key`` 会直接报错提示去配置模型。
"""

import os


# 默认响应 token 限制（与具体模型无关，保留为全局上限）
DEFAULT_RESPONSE_MAX_TOKENS = int(os.environ.get("AGENT_DEFAULT_RESPONSE_MAX_TOKENS", "32768"))

__all__ = [
    "DEFAULT_RESPONSE_MAX_TOKENS",
]
