# modules/search_providers/base.py - 搜索服务商适配器基类
#
# 统一契约：
# - 入参（SearchEngine 已完成校验与归一化）：query + params dict，键固定为
#   max_results / topic(general|news|finance) / time_range(day|week|month|year)
#   / days(int) / start_date / end_date(YYYY-MM-DD) / country / include_domains(list[str])
# - credential：api_key 类服务商为密钥字符串；SearXNG 为实例 base_url
# - 出参 dict：
#     成功 {"ok": True, "results": [{title,url,content,published_date,score}], "answer": str, "dropped": [...]}
#     失败 {"ok": False, "error": str}
#   dropped = 服务商不支持而被丢弃的入参名列表（由 SearchEngine 透传到 filters 展示）

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

from modules.i18n import tr

_REQUEST_TIMEOUT = 30


class SearchProvider:
    """搜索服务商适配器基类。子类只需实现 build_request/parse_response。"""

    name: str = "base"
    # api_key = 需要密钥；base_url = 需要实例地址（SearXNG）
    auth_kind: str = "api_key"
    # 能力声明：不支持的入参在适配时丢弃并记入 dropped
    supports_topic_news: bool = False
    supports_topic_finance: bool = False
    supports_time_range: bool = False
    supports_days: bool = False
    supports_date_range: bool = False
    supports_country: bool = False
    supports_include_domains: bool = False
    max_results_limit: int = 10

    # ------------------------------------------------------------ 统一入口

    async def search(
        self,
        query: str,
        params: Dict[str, Any],
        credential: str,
    ) -> Dict[str, Any]:
        dropped = self._drop_unsupported(params)
        try:
            method, url, kwargs = self.build_request(query, params, credential)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                if method == "GET":
                    resp = await client.get(url, **kwargs)
                else:
                    resp = await client.post(url, **kwargs)
        except httpx.TimeoutException:
            return {"ok": False, "error": tr("search_engine.search_timeout")}
        except Exception as exc:
            return {"ok": False, "error": tr("search_engine.search_failed", error=str(exc))}

        if resp.status_code != 200:
            # 非 200 也尝试解析响应体中的业务错误消息（如余额不足/key 无效/限流），
            # 避免只展示干巴巴的状态码；解析失败回退通用状态码文案
            detail = None
            try:
                detail = self.extract_error(resp.json())
            except Exception:
                detail = None
            if detail:
                return {
                    "ok": False,
                    "error": tr(
                        "search_engine.api_request_failed_detail",
                        status_code=resp.status_code,
                        detail=detail,
                    ),
                }
            return {
                "ok": False,
                "error": tr("search_engine.api_request_failed", status_code=resp.status_code),
            }
        try:
            body = resp.json()
        except Exception:
            return {"ok": False, "error": tr("search_engine.search_failed", error="bad_json")}

        error = self.extract_error(body)
        if error:
            return {"ok": False, "error": error}

        results = self.parse_response(body, params)
        return {"ok": True, "results": results, "answer": self.extract_answer(body), "dropped": dropped}

    # ------------------------------------------------------------ 子类实现

    def build_request(
        self, query: str, params: Dict[str, Any], credential: str
    ) -> Tuple[str, str, Dict[str, Any]]:
        """返回 (HTTP 方法, URL, httpx kwargs)。"""
        raise NotImplementedError

    def parse_response(self, body: Any, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """把原始响应归一化为 [{title,url,content,published_date,score}]。"""
        raise NotImplementedError

    def extract_error(self, body: Any) -> Optional[str]:
        """HTTP 200 但业务层报错时返回错误消息；默认无。"""
        return None

    def extract_answer(self, body: Any) -> str:
        """服务商级综合答案（如 Tavily answer）；默认空。"""
        return ""

    # ------------------------------------------------------------ 公共工具

    def _drop_unsupported(self, params: Dict[str, Any]) -> List[str]:
        dropped: List[str] = []
        topic = params.get("topic") or "general"
        if topic == "news" and not self.supports_topic_news:
            dropped.append("topic")
        if topic == "finance" and not self.supports_topic_finance:
            dropped.append("topic")
        if params.get("time_range") and not self.supports_time_range:
            dropped.append("time_range")
        if params.get("days") is not None and not self.supports_days:
            dropped.append("days")
        if (params.get("start_date") or params.get("end_date")) and not self.supports_date_range:
            dropped.append("start_date")
        if params.get("country") and not self.supports_country:
            dropped.append("country")
        if params.get("include_domains") and not self.supports_include_domains:
            dropped.append("include_domains")
        return dropped

    @staticmethod
    def clamp_max_results(params: Dict[str, Any], limit: int, default: int = 10) -> int:
        raw = params.get("max_results")
        try:
            value = int(raw) if raw else default
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, limit))


def time_range_to_start_date(time_range: str) -> Optional[str]:
    """day/week/month/year → 起始日期 YYYY-MM-DD（供只支持日期下界的服务商）。"""
    days = {"day": 1, "week": 7, "month": 30, "year": 365}.get(str(time_range or ""))
    if not days:
        return None
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def days_to_start_date(days: Any) -> Optional[str]:
    try:
        value = int(days)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return (datetime.now() - timedelta(days=value)).strftime("%Y-%m-%d")


def normalize_result(
    title: Any,
    url: Any,
    content: Any,
    published_date: Any = "",
    score: Any = 0,
) -> Optional[Dict[str, Any]]:
    """单条结果归一化；url 为空时丢弃该条。"""
    url_text = str(url or "").strip()
    if not url_text:
        return None
    try:
        score_value = float(score or 0)
    except (TypeError, ValueError):
        score_value = 0
    return {
        "title": str(title or "").strip() or "无标题",
        "url": url_text,
        "content": str(content or "").strip(),
        "published_date": str(published_date or "").strip(),
        "score": score_value,
    }
