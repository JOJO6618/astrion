# modules/webpage_extractor.py - 网页内容提取模块

import httpx
import json
from typing import Dict, Any, List, Union, Tuple
from utils.logger import setup_logger

logger = setup_logger(__name__)

async def tavily_extract(urls: Union[str, List[str]], api_key: str, extract_depth: str = "basic", max_urls: int = 1) -> Dict[str, Any]:
    """
    执行Tavily网页内容提取
    
    Args:
        urls: 要提取的URL（字符串或列表）
        api_key: Tavily API密钥
        extract_depth: 提取深度 (basic/advanced)
        max_urls: 最大提取URL数量
    
    Returns:
        提取结果字典
    """
    if not api_key:
        return {"error": "Tavily API密钥未配置"}

    # 确保urls是列表
    if isinstance(urls, str):
        urls = [urls]

    # 限制URL数量
    urls = urls[:max_urls]

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.tavily.com/extract",
                json={
                    "urls": urls,
                    "extract_depth": extract_depth,
                    "include_images": False,
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=60,
            )

            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"API请求失败: HTTP {response.status_code}"}

    except httpx.TimeoutException:
        return {"error": "请求超时，网页响应过慢"}
    except httpx.RequestError as e:
        return {"error": f"网络请求错误: {str(e)}"}
    except Exception as e:
        logger.error(f"网页提取异常: {e}")
        return {"error": f"提取异常: {str(e)}"}


def format_extract_results(results: Dict[str, Any]) -> str:
    """
    格式化提取结果为简洁版本
    
    Args:
        results: tavily_extract返回的结果
    
    Returns:
        格式化后的内容字符串
    """
    if "error" in results:
        return f"❌ 提取失败: {results['error']}"

    if not results.get("results"):
        return "❌ 未能提取到任何内容"

    formatted_parts = []
    
    # 成功提取的结果
    for i, result in enumerate(results["results"], 1):
        url = result.get("url", "N/A")
        raw_content = result.get("raw_content", "").strip()
        
        if raw_content:
            content_length = len(raw_content)
            formatted_parts.append(f"🌐 网页内容 ({content_length} 字符):")
            formatted_parts.append(f"📍 URL: {url}")
            formatted_parts.append("=" * 50)
            formatted_parts.append(raw_content)
            formatted_parts.append("=" * 50)
        else:
            formatted_parts.append(f"⚠️ URL {url} 提取到空内容")

    # 失败的URL（如果有）
    if results.get("failed_results"):
        formatted_parts.append("\n❌ 提取失败的URL:")
        for failed in results["failed_results"]:
            formatted_parts.append(f"- {failed.get('url', 'N/A')}: {failed.get('error', '未知错误')}")

    return "\n".join(formatted_parts)


async def extract_webpage_content(urls: Union[str, List[str]], api_key: str, extract_depth: str = "basic", max_urls: int = 1) -> Tuple[str, str]:
    """
    完整的网页内容提取流程
    
    Args:
        urls: 要提取的URL（字符串或列表）
        api_key: Tavily API密钥
        extract_depth: 提取深度 (basic/advanced)
        max_urls: 最大提取URL数量
    
    Returns:
        (完整内容, 完整内容) - 为了兼容性返回相同内容两份
    """
    # 执行提取
    results = await tavily_extract(urls, api_key, extract_depth, max_urls)
    
    # 格式化结果
    formatted_content = format_extract_results(results)
    
    # 返回相同内容（简化版本，不需要长短版本区分）
    return formatted_content, formatted_content