# modules/search_engine.py - 网络搜索模块

import httpx
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


class SearchEngine:
    def __init__(self):
        self.api_key = TAVILY_API_KEY
        self.api_url = "https://api.tavily.com/search"
        
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
        if not self.api_key or self.api_key == "your-tavily-api-key":
            return {
                "success": False,
                "error": tr("search_engine.api_key_not_configured"),
                "results": []
            }
        
        validation = self._build_payload(
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
        
        if not validation["success"]:
            return validation
        
        payload = validation["payload"]
        applied_filters = validation["filters"]
        
        max_results = payload.get("max_results", SEARCH_MAX_RESULTS)
        
        print(f"{OUTPUT_FORMATS['search']} 搜索: {query}")
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self.api_url,
                    json={
                        **payload
                    },
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    }
                )
                
                if response.status_code != 200:
                    return {
                        "success": False,
                        "error": tr("search_engine.api_request_failed", status_code=response.status_code),
                        "results": []
                    }
                
                data = response.json()
                
                # 格式化结果
                formatted_results = self._format_results(data, applied_filters)
                
                print(f"{OUTPUT_FORMATS['success']} 搜索完成，找到 {len(formatted_results['results'])} 条结果")
                
                return formatted_results
                
        except httpx.TimeoutException:
            return {
                "success": False,
                "error": tr("search_engine.search_timeout"),
                "results": []
            }
        except Exception as e:
            return {
                "success": False,
                "error": tr("search_engine.search_failed", error=str(e)),
                "results": []
            }
    
    def _format_results(self, raw_data: Dict, filters: Dict[str, Any]) -> Dict:
        """格式化搜索结果"""
        formatted = {
            "success": True,
            "query": raw_data.get("query", ""),
            "answer": raw_data.get("answer", ""),
            "results": [],
            "timestamp": datetime.now().isoformat(),
            "filters": filters,
            "total_results": len(raw_data.get("results", []))
        }
        
        # 处理每个搜索结果
        for idx, result in enumerate(raw_data.get("results", []), 1):
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

    def _build_payload(
        self,
        query: str,
        max_results: Optional[int],
        topic: Optional[str],
        time_range: Optional[str],
        days: Optional[int],
        start_date: Optional[str],
        end_date: Optional[str],
        country: Optional[str],
        include_domains: Optional[List[str]]
    ) -> Dict[str, Any]:
        """验证并构建 Tavily 请求参数"""
        payload: Dict[str, Any] = {
            "query": query,
            "search_depth": "advanced",
            "include_answer": False,
            "include_images": False,
            "include_raw_content": False
        }
        
        filters: Dict[str, Any] = {}
        
        if max_results:
            payload["max_results"] = max_results
        else:
            payload["max_results"] = SEARCH_MAX_RESULTS
        
        normalized_topic = (topic or "general").strip().lower()
        if not normalized_topic:
            normalized_topic = "general"
        if normalized_topic not in self._valid_topics:
            return {
                "success": False,
                "error": tr("search_engine.invalid_topic", topic=topic, valid=", ".join(self._valid_topics)),
                "results": []
            }
        payload["topic"] = normalized_topic
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
            payload["days"] = days_value
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
            payload["time_range"] = normalized_range
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
            payload["start_date"] = start_date
            payload["end_date"] = end_date
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
                payload["country"] = normalized_country
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
                payload["include_domains"] = cleaned_domains
                filters["include_domains"] = cleaned_domains
        
        return {
            "success": True,
            "payload": payload,
            "filters": filters,
            "results": []
        }

    def _summarize_filters(self, filters: Dict[str, Any]) -> str:
        """构建过滤条件摘要"""
        if not filters:
            return ""
        
        parts = []
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
        
        if not parts:
            return ""
        
        return "🎯 过滤条件: " + " | ".join(parts)
