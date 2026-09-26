# modules/search_providers/bocha.py - 博查搜索适配器（国产主源）
#
# 官方文档：https://open.bochaai.com
# 响应为 Bing 兼容结构；外层可能有 {"code","msg","data"} 包装也可能是裸 SearchResponse，
# 解析两路兼容。时间过滤用 freshness 枚举或自定义区间字符串。

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from modules.search_providers.base import SearchProvider, days_to_start_date, normalize_result

_FRESHNESS_MAP = {
    "day": "oneDay",
    "week": "oneWeek",
    "month": "oneMonth",
    "year": "oneYear",
}


class BochaProvider(SearchProvider):
    name = "bocha"
    auth_kind = "api_key"
    supports_time_range = True   # freshness 枚举
    supports_days = True         # 换算为自定义日期区间
    supports_date_range = True   # freshness=YYYY-MM-DD..YYYY-MM-DD
    supports_include_domains = True  # include 参数（| 分隔）
    max_results_limit = 50

    def build_request(
        self, query: str, params: Dict[str, Any], credential: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "query": query,
            "summary": True,  # 开启长摘要作为 content（否则只有 snippet）
            "count": self.clamp_max_results(params, self.max_results_limit, 10),
            "freshness": "noLimit",
        }
        if params.get("time_range"):
            payload["freshness"] = _FRESHNESS_MAP.get(params["time_range"], "noLimit")
        elif params.get("start_date") and params.get("end_date"):
            payload["freshness"] = f"{params['start_date']}..{params['end_date']}"
        elif params.get("days") is not None:
            start = days_to_start_date(params["days"])
            if start:
                from datetime import datetime

                payload["freshness"] = f"{start}..{datetime.now().strftime('%Y-%m-%d')}"
        if params.get("include_domains"):
            payload["include"] = "|".join(params["include_domains"][:100])
        return (
            "POST",
            "https://api.bochaai.com/v1/web-search",
            {
                "json": payload,
                "headers": {
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                },
            },
        )

    @staticmethod
    def _unwrap(body: Any) -> Dict[str, Any]:
        """兼容两种外层形态：{"code","msg","data":{...}} 或裸 SearchResponse。"""
        if isinstance(body, dict) and isinstance(body.get("data"), dict):
            return body["data"]
        return body if isinstance(body, dict) else {}

    def extract_error(self, body: Any) -> str | None:
        if not isinstance(body, dict):
            return None
        code = body.get("code")
        if code is not None and code != 200:
            msg = body.get("msg") or body.get("message") or body.get("error") or f"code_{code}"
            return str(msg)
        return None

    def parse_response(self, body: Any, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        data = self._unwrap(body)
        value = ((data.get("webPages") or {}).get("value")) or []
        results: List[Dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            content = item.get("summary") or item.get("snippet") or ""
            normalized = normalize_result(
                item.get("name"),
                item.get("url"),
                content,
                item.get("datePublished"),
            )
            if normalized:
                results.append(normalized)
        return results
