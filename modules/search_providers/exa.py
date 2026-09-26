# modules/search_providers/exa.py - Exa 搜索适配器（语义搜索，国际源）
#
# 官方文档：https://exa.ai/docs/reference/search
# 要点：正文需显式索取（contents.text），否则结果只有 title/url；
# 时间过滤为 ISO 起止日期；news 映射 category="news"，finance 映射 "financial report"。

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from modules.search_providers.base import (
    SearchProvider,
    days_to_start_date,
    normalize_result,
    time_range_to_start_date,
)

_CATEGORY_MAP = {"news": "news", "finance": "financial report"}


class ExaProvider(SearchProvider):
    name = "exa"
    auth_kind = "api_key"
    supports_topic_news = True
    supports_topic_finance = True
    supports_time_range = True
    supports_days = True
    supports_date_range = True
    supports_include_domains = True
    max_results_limit = 100

    def build_request(
        self, query: str, params: Dict[str, Any], credential: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "query": query,
            "type": "auto",
            "numResults": self.clamp_max_results(params, self.max_results_limit, 10),
            # 显式索取正文摘要（否则 content 为空）；text 单视图避免双重计费
            "contents": {"text": {"maxCharacters": 800}},
        }
        topic = params.get("topic") or "general"
        category = _CATEGORY_MAP.get(topic)
        if category:
            payload["category"] = category

        start = None
        end = None
        if params.get("time_range"):
            start = time_range_to_start_date(params["time_range"])
        elif params.get("start_date") and params.get("end_date"):
            start, end = params["start_date"], params["end_date"]
        elif params.get("days") is not None:
            start = days_to_start_date(params["days"])
        if start:
            payload["startPublishedDate"] = f"{start}T00:00:00.000Z"
        if end:
            payload["endPublishedDate"] = f"{end}T23:59:59.000Z"

        if params.get("include_domains"):
            payload["includeDomains"] = params["include_domains"]
        return (
            "POST",
            "https://api.exa.ai/search",
            {
                "json": payload,
                "headers": {
                    "x-api-key": credential,
                    "Content-Type": "application/json",
                },
            },
        )

    def extract_error(self, body: Any) -> str | None:
        if isinstance(body, dict) and body.get("error"):
            return str(body.get("error"))
        return None

    def parse_response(self, body: Any, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for item in (body or {}).get("results") or []:
            if not isinstance(item, dict):
                continue
            normalized = normalize_result(
                item.get("title"),
                item.get("url"),
                item.get("text"),
                item.get("publishedDate"),
                item.get("score"),
            )
            if normalized:
                results.append(normalized)
        return results
