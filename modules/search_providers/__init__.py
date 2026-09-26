# modules/search_providers/__init__.py - 搜索服务商注册表与凭证解析
#
# 新增服务商 checklist：
# 1. 新建 <name>.py 实现 SearchProvider 子类并在下方 _PROVIDERS 注册
# 2. config/search.py 加环境变量兜底（如需）
# 3. personalization 双注册（DEFAULT + sanitize）＋ stores/personalization.ts 三处
# 4. SearchTab.vue 设置项 + locales 双语文案（provider 名称 key：personalization.searchProviderXxx）
# 5. modules/i18n_messages/search_engine.py 如需新错误文案

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from modules.search_providers.base import SearchProvider
from modules.search_providers.bocha import BochaProvider
from modules.search_providers.exa import ExaProvider
from modules.search_providers.parallel import ParallelProvider
from modules.search_providers.searxng import SearXNGProvider
from modules.search_providers.tavily import TavilyProvider

_PROVIDERS: Dict[str, SearchProvider] = {
    p.name: p
    for p in (
        TavilyProvider(),
        BochaProvider(),
        ExaProvider(),
        ParallelProvider(),
        SearXNGProvider(),
    )
}

DEFAULT_PROVIDER = "tavily"

# personalization 键名与环境变量兜底（auth_kind=api_key 为密钥，base_url 为实例地址）
_CREDENTIAL_SOURCES: Dict[str, Tuple[str, str]] = {
    "tavily": ("tavily_api_key", "TAVILY_API_KEY"),
    "bocha": ("bocha_api_key", "BOCHA_API_KEY"),
    "exa": ("exa_api_key", "EXA_API_KEY"),
    "parallel": ("parallel_api_key", "PARALLEL_API_KEY"),
    "searxng": ("searxng_base_url", "SEARXNG_BASE_URL"),
}


def list_search_providers() -> Dict[str, SearchProvider]:
    return dict(_PROVIDERS)


def get_search_provider(name: str) -> Optional[SearchProvider]:
    return _PROVIDERS.get(name)


def resolve_search_provider_name(prefs: Optional[Dict[str, Any]] = None) -> str:
    """从个性化配置解析当前搜索服务商；非法值回退默认。"""
    name = str((prefs or {}).get("search_provider") or "").strip().lower()
    return name if name in _PROVIDERS else DEFAULT_PROVIDER


def resolve_search_credential(provider_name: str, prefs: Optional[Dict[str, Any]] = None) -> str:
    """解析服务商凭证：personalization UI 配置优先，环境变量兜底。"""
    source = _CREDENTIAL_SOURCES.get(provider_name)
    if not source:
        return ""
    prefs_key, env_name = source
    ui_value = str((prefs or {}).get(prefs_key) or "").strip()
    if ui_value:
        return ui_value
    try:
        import config as _config

        return str(getattr(_config, env_name, "") or "").strip()
    except Exception:
        return ""
