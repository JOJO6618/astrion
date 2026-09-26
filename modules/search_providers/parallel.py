# modules/search_providers/parallel.py - Parallel 搜索适配器（国际源）
#
# 官方文档：https://docs.parallel.ai
# 要点：无单一 query 字段（objective + search_queries 必填）；
# 时间过滤仅下界 after_date；内容为摘录数组 excerpts 需拼接。

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from modules.search_providers.base import (
    SearchProvider,
    days_to_start_date,
    normalize_result,
    time_range_to_start_date,
)


class ParallelProvider(SearchProvider):
    name = "parallel"
    auth_kind = "api_key"
    supports_time_range = True   # 换算 after_date（仅下界）
    supports_days = True         # 换算 after_date
    supports_date_range = True   # 仅 start_date 生效，end_date 丢弃
    supports_include_domains = True
    max_results_limit = 20

    def _drop_unsupported(self, params: Dict[str, Any]) -> List[str]:
        dropped = super()._drop_unsupported(params)
        # date_range 仅能保留下界：end_date 单独记账
        if params.get("end_date") and "start_date" not in dropped:
            dropped.append("end_date")
        return dropped

    def build_request(
        self, query: str, params: Dict[str, Any], credential: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        source_policy: Dict[str, Any] = {}
        after = None
        if params.get("time_range"):
            after = time_range_to_start_date(params["time_range"])
        elif params.get("start_date"):
            after = params["start_date"]
        elif params.get("days") is not None:
            after = days_to_start_date(params["days"])
        if after:
            source_policy["after_date"] = after
        if params.get("include_domains"):
            source_policy["include_domains"] = params["include_domains"][:200]

        payload: Dict[str, Any] = {
            "objective": query,
            "search_queries": [query],
            "mode": "fast",  # $1/1k、低延迟档；advanced 质量更高但 5 倍价
            "max_results": self.clamp_max_results(params, self.max_results_limit, 10),
        }
        if source_policy:
            payload["advanced_settings"] = {"source_policy": source_policy}
        return (
            "POST",
            "https://api.parallel.ai/v1/search",
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
            excerpts = item.get("excerpts")
            content = "\n".join(str(e) for e in excerpts if e) if isinstance(excerpts, list) else ""
            normalized = normalize_result(
                item.get("title"),
                item.get("url"),
                content,
                item.get("publish_date"),
            )
            if normalized:
                results.append(normalized)
        return results
