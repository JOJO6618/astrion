# modules/search_providers/tavily.py - Tavily 搜索适配器（现状基线，能力最全）

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from modules.search_providers.base import SearchProvider, normalize_result


class TavilyProvider(SearchProvider):
    name = "tavily"
    auth_kind = "api_key"
    supports_topic_news = True
    supports_topic_finance = True
    supports_time_range = True
    supports_days = True
    supports_date_range = True
    supports_country = True
    supports_include_domains = True
    max_results_limit = 20

    def build_request(
        self, query: str, params: Dict[str, Any], credential: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "query": query,
            "search_depth": "advanced",
            "include_answer": False,
            "include_images": False,
            "include_raw_content": False,
            "max_results": self.clamp_max_results(params, self.max_results_limit, 10),
            "topic": params.get("topic") or "general",
        }
        if params.get("time_range"):
            payload["time_range"] = params["time_range"]
        if params.get("days") is not None:
            payload["days"] = params["days"]
        if params.get("start_date") and params.get("end_date"):
            payload["start_date"] = params["start_date"]
            payload["end_date"] = params["end_date"]
        if params.get("country"):
            payload["country"] = params["country"]
        if params.get("include_domains"):
            payload["include_domains"] = params["include_domains"]
        return (
            "POST",
            "https://api.tavily.com/search",
            {
                "json": payload,
                "headers": {
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                },
            },
        )

    def parse_response(self, body: Any, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for item in (body or {}).get("results") or []:
            if not isinstance(item, dict):
                continue
            normalized = normalize_result(
                item.get("title"),
                item.get("url"),
                item.get("content"),
                item.get("published_date"),
                item.get("score"),
            )
            if normalized:
                results.append(normalized)
        return results

    def extract_answer(self, body: Any) -> str:
        return str((body or {}).get("answer") or "")
