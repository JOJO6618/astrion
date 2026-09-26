# modules/search_engine.py - 网络搜索模块（多服务商调度层）
#
# 架构：本模块负责参数校验/归一化、凭证解析、结果包装与摘要；
# 各服务商的请求构造与响应解析在 modules/search_providers/ 适配器包中。

import json
from typing import Dict, Optional, Any, List
from datetime import datetime
from pathlib import Path
import re
from urllib.parse import urlparse
try:
    from config import TAVILY_API_KEY, SEARCH_MAX_RESULTS, OUTPUT_FORMATS, DATA_DIR
except ImportError:
    import sys
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import TAVILY_API_KEY, SEARCH_MAX_RESULTS, OUTPUT_FORMATS, DATA_DIR

from modules.i18n import tr
from modules.search_providers import (
    get_search_provider,
    resolve_search_credential,
    resolve_search_provider_name,
)


def resolve_tavily_api_key(prefs: Optional[Dict[str, Any]] = None, data_dir=None) -> str:
    """运行时解析 Tavily API 密钥。

    优先级：设置页 UI 配置（personalization.tavily_api_key）> 环境变量（config.TAVILY_API_KEY）。
    - prefs：已加载的个性化配置 dict（调用方已持有时传入，避免重复读盘）；
    - data_dir：未传 prefs 时按数据目录现读 personalization.json；两者都不传则只用环境变量。
    """
    if prefs is None and data_dir is not None:
        try:
            from modules.personalization_manager import load_personalization_config
            prefs = load_personalization_config(data_dir) or {}
        except Exception:
            prefs = {}
    ui_key = str((prefs or {}).get("tavily_api_key") or "").strip()
    if ui_key:
        return ui_key
    return TAVILY_API_KEY or ""


class SearchEngine:
    def __init__(self, data_dir=None):
        # data_dir 用于运行时读取当前用户个性化配置（搜索服务商选择与各家密钥）；
        # 未提供时退化为默认服务商 + 进程启动时的环境变量密钥（旧行为）。
        self.data_dir = data_dir

        self._valid_topics = {"general", "news", "finance"}
        self._valid_time_ranges = {
            "day": "day",
            "d": "day",
            "week": "week",
            "w": "week",
            "month": "month",
            "m": "month",
            "year": "year",
            "y": "year"
        }
        self._date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        
    async def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        topic: Optional[str] = None,
        time_range: Optional[str] = None,
        days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        country: Optional[str] = None,
        include_domains: Optional[List[str]] = None
    ) -> Dict:
        """
        执行网络搜索
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            topic: 搜索类型（general/news/finance）
            time_range: 相对时间范围（day/week/month/year 或 d/w/m/y）
            days: 过去N天，仅topic=news可用
            start_date: 起始日期，格式YYYY-MM-DD
            end_date: 结束日期，格式YYYY-MM-DD
            country: 国家过滤，仅topic=general可用
            include_domains: 仅包含这些域名（最多300个）
        
        Returns:
            搜索结果字典
        """
        prefs: Dict[str, Any] = {}
        if self.data_dir is not None:
            try:
                from modules.personalization_manager import load_personalization_config
                prefs = load_personalization_config(self.data_dir) or {}
            except Exception:
                prefs = {}

        provider_name = resolve_search_provider_name(prefs)
        provider = get_search_provider(provider_name)
        if provider is None:  # 理论不可达（resolve 已回退默认），防御
            provider_name = "tavily"
            provider = get_search_provider("tavily")
        credential = resolve_search_credential(provider_name, prefs)
        if not credential or credential == "your-tavily-api-key":
            error_key = (
                "search_engine.base_url_not_configured"
                if provider.auth_kind == "base_url"
                else "search_engine.api_key_not_configured"
            )
            return {
                "success": False,
                "error": tr(error_key, provider=provider_name),
                "results": []
            }

        validation = self._validate_params(
            max_results=max_results,
            topic=topic,
            time_range=time_range,
            days=days,
            start_date=start_date,
            end_date=end_date,
            country=country,
            include_domains=include_domains
        )

        if not validation["success"]:
            return validation

        normalized_params = validation["params"]
        applied_filters = validation["filters"]
        applied_filters["provider"] = provider_name

        print(f"{OUTPUT_FORMATS['search']} 搜索({provider_name}): {query}")

        outcome = await provider.search(query, normalized_params, credential)
        if not outcome.get("ok"):
            return {
                "success": False,
                "error": outcome.get("error") or tr("search_engine.unknown_error"),
                "results": []
            }

        dropped = outcome.get("dropped") or []
        if dropped:
            applied_filters["provider_dropped"] = dropped

        formatted_results = self._wrap_results(
            query,
            outcome.get("results") or [],
            applied_filters,
            answer=outcome.get("answer") or "",
        )

        print(f"{OUTPUT_FORMATS['success']} 搜索完成，找到 {len(formatted_results['results'])} 条结果")
        return formatted_results

    def _wrap_results(
        self,
        query: str,
        results: List[Dict[str, Any]],
        filters: Dict[str, Any],
        answer: str = ""
    ) -> Dict:
        """把适配器归一化结果包装为对外契约（加序号与 domain 字段）。"""
        formatted = {
            "success": True,
            "query": query,
            "answer": answer,
            "results": [],
            "timestamp": datetime.now().isoformat(),
            "filters": filters,
            "total_results": len(results)
        }

        for idx, result in enumerate(results, 1):
            url = result.get("url", "")
            formatted_result = {
                "index": idx,
                "title": result.get("title", "无标题"),
                "url": url,
                "domain": urlparse(url).netloc.lower() if url else "",
                "content": result.get("content", ""),
                "score": result.get("score", 0),
                "published_date": result.get("published_date", "")
            }
            formatted["results"].append(formatted_result)

        return formatted

    def build_summary_text(
        self,
        query: str,
        results: List[Dict[str, Any]],
        filters: Dict[str, Any],
        timestamp: str
    ) -> str:
        """构建给模型看的搜索摘要文本。

        若结果项带 citation_id（tools_execution 注册 citation 后回填），
        标题行会带 [src_xxx] 前缀，供模型在行内引用中使用。
        """
        summary_lines = [
            f"🔍 搜索查询: {query}",
            f"📅 搜索时间: {timestamp}"
        ]
        
        filter_notes = self._summarize_filters(filters or {})
        if filter_notes:
            summary_lines.append(filter_notes)
        summary_lines.append("")
        
        # 添加搜索结果
        if results:
            summary_lines.append("📊 搜索结果:")
            
            for result in results:
                cid = result.get("citation_id")
                title_line = f"\n{result['index']}. [{cid}] {result['title']}" if cid else f"\n{result['index']}. {result['title']}"
                summary_lines.extend([
                    title_line,
                    f"   🔗 {result['url']}",
                    f"   📄 {result['content'][:200]}..." if len(result['content']) > 200 else f"   📄 {result['content']}",
                ])
                
                if result.get("published_date"):
                    summary_lines.append(f"   📅 发布时间: {result['published_date']}")
        else:
            summary_lines.append("未找到相关结果")
        
        return "\n".join(summary_lines)
    
    async def search_with_summary(
        self,
        query: str,
        max_results: Optional[int] = None,
        topic: Optional[str] = None,
        time_range: Optional[str] = None,
        days: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        country: Optional[str] = None,
        include_domains: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        搜索并返回格式化的摘要
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
        
        Returns:
            格式化的搜索摘要字符串
        """
        results = await self.search(
            query=query,
            max_results=max_results,
            topic=topic,
            time_range=time_range,
            days=days,
            start_date=start_date,
            end_date=end_date,
            country=country,
            include_domains=include_domains
        )
        
        if not results["success"]:
            return {
                "success": False,
                "error": results.get("error", tr("search_engine.unknown_error")),
                "summary": ""
            }
        
        return {
            "success": True,
            "summary": self.build_summary_text(
                query,
                results["results"],
                results.get("filters", {}),
                results["timestamp"]
            ),
            "timestamp": results["timestamp"],
            "filters": results.get("filters", {}),
            "query": results.get("query", query),
            "results": results.get("results", []),
            "total_results": results.get("total_results", len(results.get("results", [])))
        }
    
    async def quick_answer(self, query: str) -> str:
        """
        快速获取答案（返回首个搜索结果的摘要）
        
        Args:
            query: 查询问题
        
        Returns:
            首个结果摘要或错误信息
        """
        results = await self.search(query, max_results=5)
        
        if not results["success"]:
            return tr("search_engine.search_failed", error=results["error"])
        
        # 返回第一个结果的摘要
        if results["results"]:
            first_result = results["results"][0]
            return f"{first_result['title']}\n{first_result['content'][:300]}..."
        
        return tr("search_engine.no_relevant_info")
    
    def save_results(self, results: Dict, filename: str = None) -> str:
        """
        保存搜索结果到文件
        
        Args:
            results: 搜索结果
            filename: 文件名（可选）
        
        Returns:
            保存的文件路径
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"search_{timestamp}.json"
        
        file_path = Path(DATA_DIR).expanduser().resolve() / "searches" / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 保存结果
        with file_path.open('w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        print(f"{OUTPUT_FORMATS['file']} 搜索结果已保存到: {file_path}")
        
        return str(file_path)
    
    def load_results(self, filename: str) -> Optional[Dict]:
        """
        加载之前的搜索结果
        
        Args:
            filename: 文件名
        
        Returns:
            搜索结果字典或None
        """
        file_path = Path(DATA_DIR).expanduser().resolve() / "searches" / filename
        
        try:
            with file_path.open('r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"{OUTPUT_FORMATS['error']} 文件不存在: {file_path}")
            return None
        except Exception as e:
            print(f"{OUTPUT_FORMATS['error']} 加载失败: {e}")
            return None

    def _validate_params(
        self,
        max_results: Optional[int],
        topic: Optional[str],
        time_range: Optional[str],
        days: Optional[int],
        start_date: Optional[str],
        end_date: Optional[str],
        country: Optional[str],
        include_domains: Optional[List[str]]
    ) -> Dict[str, Any]:
        """校验并归一化搜索参数（服务商无关）；具体请求构造在各适配器中。"""
        params: Dict[str, Any] = {}

        filters: Dict[str, Any] = {}

        if max_results:
            params["max_results"] = max_results
        else:
            params["max_results"] = SEARCH_MAX_RESULTS

        normalized_topic = (topic or "general").strip().lower()
        if not normalized_topic:
            normalized_topic = "general"
        if normalized_topic not in self._valid_topics:
            return {
                "success": False,
                "error": tr("search_engine.invalid_topic", topic=topic, valid=", ".join(self._valid_topics)),
                "results": []
            }
        params["topic"] = normalized_topic
        filters["topic"] = normalized_topic

        # 时间参数互斥检查
        has_time_range = bool(time_range)
        has_days = days is not None
        has_date_range = bool(start_date or end_date)
        selected_filters = sum([has_time_range, has_days, has_date_range])
        if selected_filters > 1:
            return {
                "success": False,
                "error": tr("search_engine.time_params_mutually_exclusive"),
                "results": []
            }

        # 验证 days
        if has_days:
            try:
                days_value = int(days)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return {
                    "success": False,
                    "error": tr("search_engine.days_must_be_positive_int", days=days),
                    "results": []
                }
            if days_value <= 0:
                return {
                    "success": False,
                    "error": tr("search_engine.days_must_be_greater_than_zero", days=days_value),
                    "results": []
                }
            if normalized_topic != "news":
                return {
                    "success": False,
                    "error": tr("search_engine.days_only_for_news"),
                    "results": []
                }
            params["days"] = days_value
            filters["days"] = days_value

        # 验证 time_range
        if has_time_range:
            normalized_range = time_range.strip().lower()  # type: ignore[union-attr]
            normalized_range = self._valid_time_ranges.get(normalized_range, "")
            if not normalized_range:
                return {
                    "success": False,
                    "error": tr("search_engine.invalid_time_range", time_range=time_range),
                    "results": []
                }
            params["time_range"] = normalized_range
            filters["time_range"] = normalized_range

        # 验证日期范围
        if has_date_range:
            if not start_date or not end_date:
                return {
                    "success": False,
                    "error": tr("search_engine.date_range_requires_both"),
                    "results": []
                }
            if not self._date_pattern.match(start_date):
                return {
                    "success": False,
                    "error": tr("search_engine.start_date_invalid_format", start_date=start_date),
                    "results": []
                }
            if not self._date_pattern.match(end_date):
                return {
                    "success": False,
                    "error": tr("search_engine.end_date_invalid_format", end_date=end_date),
                    "results": []
                }
            try:
                start_dt = datetime.fromisoformat(start_date)
                end_dt = datetime.fromisoformat(end_date)
            except ValueError:
                return {
                    "success": False,
                    "error": tr("search_engine.invalid_calendar_date"),
                    "results": []
                }
            if start_dt > end_dt:
                return {
                    "success": False,
                    "error": tr("search_engine.start_date_after_end_date", start_date=start_date, end_date=end_date),
                    "results": []
                }
            params["start_date"] = start_date
            params["end_date"] = end_date
            filters["start_date"] = start_date
            filters["end_date"] = end_date

        # 国家过滤
        if country:
            normalized_country = country.strip().lower()
            if normalized_country:
                if normalized_topic != "general":
                    return {
                        "success": False,
                        "error": tr("search_engine.country_only_for_general"),
                        "results": []
                    }
                params["country"] = normalized_country
                filters["country"] = normalized_country

        # 域名白名单
        if include_domains is not None:
            if not isinstance(include_domains, list):
                return {
                    "success": False,
                    "error": tr("search_engine.include_domains_must_be_array"),
                    "results": []
                }
            cleaned_domains = []
            for item in include_domains:
                if not isinstance(item, str):
                    return {
                        "success": False,
                        "error": tr("search_engine.include_domains_item_must_be_string"),
                        "results": []
                    }
                domain = item.strip().lower()
                if domain:
                    cleaned_domains.append(domain)
            if len(cleaned_domains) > 300:
                return {
                    "success": False,
                    "error": tr("search_engine.include_domains_too_many", count=len(cleaned_domains)),
                    "results": []
                }
            if cleaned_domains:
                params["include_domains"] = cleaned_domains
                filters["include_domains"] = cleaned_domains

        return {
            "success": True,
            "params": params,
            "filters": filters,
            "results": []
        }

    def _summarize_filters(self, filters: Dict[str, Any]) -> str:
        """构建过滤条件摘要（含服务商与不支持被忽略的参数）"""
        if not filters:
            return ""

        parts = []
        provider = filters.get("provider")
        if provider:
            parts.append(f"Provider: {provider}")
        topic = filters.get("topic")
        if topic:
            parts.append(f"Topic: {topic}")

        if "time_range" in filters:
            parts.append(f"Time Range: {filters['time_range']}")
        elif "days" in filters:
            parts.append(f"最近 {filters['days']} 天")
        elif "start_date" in filters and "end_date" in filters:
            parts.append(f"{filters['start_date']} 至 {filters['end_date']}")

        if "country" in filters:
            parts.append(f"Country: {filters['country']}")
        if "include_domains" in filters:
            parts.append(f"Domains: {len(filters['include_domains'])}")
        if filters.get("provider_dropped"):
            parts.append(f"该服务商不支持已忽略: {', '.join(filters['provider_dropped'])}")

        if not parts:
            return ""

        return "🎯 过滤条件: " + " | ".join(parts)
