"""Codex 订阅原生接入（host 单用户）。

设计文档：docs/codex_integration_plan.md

2026-09-25 协议泛化：请求编排（chat_codex）已消亡，Responses 调用统一走
``utils/api_client/responses/`` 通用通道；本子包只保留订阅管理面
（OAuth 凭证、模型发现、官方 instructions、用量查询）。
"""

from utils.api_client.codex.auth import (
    CodexAuthError,
    CodexAuthManager,
    get_auth_manager,
)
from utils.api_client.codex.accounts import (
    CodexAccountStore,
    get_codex_account_store,
)
from utils.api_client.codex.models import (
    CodexModelsManager,
    get_all_codex_profiles,
    get_models_manager,
)

__all__ = [
    "CodexAuthError",
    "CodexAuthManager",
    "CodexAccountStore",
    "CodexModelsManager",
    "get_auth_manager",
    "get_codex_account_store",
    "get_all_codex_profiles",
    "get_models_manager",
]
