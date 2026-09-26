# modules/search_providers/searxng.py - SearXNG 自托管元搜索适配器（免费兜底）
#
# 官方文档：https://docs.searxng.org
# 前提：实例 settings.yml 需开启 search.formats: json（默认只开 html）。
# 无 max_results 服务端参数 → 本地截断；域名白名单无原生参数 → 本地按 host 后过滤。

from __future__ import annotations

from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from modules.search_providers.base import SearchProvider, normalize_result


class SearXNGProvider(SearchProvider):
    name = "searxng"
    auth_kind = "base_url"
    supports_topic_news = True   # categories=news
    supports_time_range = True   # day/week/month/year 原生支持
    supports_days = True         # 近似映射到最近的 time_range 档
    supports_include_domains = True  # 本地后过滤实现
    max_results_limit = 50

    def build_request(
        self, query: str, params: Dict[str, Any], credential: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        base = credential.rstrip("/")
        query_params: Dict[str, Any] = {
            "q": query,
            "format": "json",
            "safesearch": 1,
        }
        if (params.get("topic") or "general") == "news":
            query_params["categories"] = "news"
        time_range = params.get("time_range")
        if not time_range and params.get("days") is not None:
            # days → 最近档位近似
            try:
                days_value = int(params["days"])
            except (TypeError, ValueError):
                days_value = 0
            if 0 < days_value <= 1:
                time_range = "day"
            elif days_value <= 7:
                time_range = "week"
            elif days_value <= 31:
                time_range = "month"
            else:
                time_range = "year"
        if time_range:
            query_params["time_range"] = time_range
        return ("GET", f"{base}/search", {"params": query_params})

    def parse_response(self, body: Any, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for item in (body or {}).get("results") or []:
            if not isinstance(item, dict):
                continue
            normalized = normalize_result(
                item.get("title"),
                item.get("url"),
                item.get("content"),
                item.get("publishedDate"),
                item.get("score"),
            )
            if normalized:
                results.append(normalized)

        # 域名白名单：无原生参数，本地按 host 后缀过滤
        include = params.get("include_domains") or []
        if include:
            allowed = {d.strip().lower() for d in include if isinstance(d, str) and d.strip()}
            if allowed:
                results = [r for r in results if self._host_allowed(r["url"], allowed)]

        # 无服务端 max_results：本地截断
        limit = self.clamp_max_results(params, self.max_results_limit, 10)
        return results[:limit]

    @staticmethod
    def _host_allowed(url: str, allowed: set) -> bool:
        try:
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            return False
        return any(host == d or host.endswith("." + d) for d in allowed)
