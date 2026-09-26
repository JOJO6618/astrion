# modules/webpage_extractor.py - 网页内容提取模块
#
# 提取分两层：
# 1. 白名单域名走本机直提（免费、零配额，唯一自动回退层）：GitHub 代码文件页
#    有专属直链适配（jsDelivr CDN → GitHub API 备用），其余白名单页面用 trafilatura。
# 2. 未命中白名单（或直提失败）走用户在设置页选定的提取商：Jina Reader（默认，
#    无 key 匿名限速可用）/ Tavily / Exa / Parallel。选定商失败即报错，
#    不再向其他家回退（避免在用户不知情时消耗多家付费额度）。
#
# trafilatura 为可选依赖：未安装时通用直提静默失效，仅 GitHub 直链仍可用。

import base64
import re
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import httpx

from utils.logger import setup_logger
from modules.i18n import tr

logger = setup_logger(__name__)

try:
    import trafilatura as _trafilatura
except ImportError:  # 可选依赖
    _trafilatura = None

# 内置直提白名单域名（个人空间可追加；子域名自动匹配）
BUILTIN_DIRECT_EXTRACT_DOMAINS: Tuple[str, ...] = ("github.com",)

# 可选网页提取商（personalization.webpage_extract_provider 的白名单值）
EXTRACT_PROVIDERS: Tuple[str, ...] = ("jina", "tavily", "exa", "parallel")
DEFAULT_EXTRACT_PROVIDER = "jina"
_EXTRACT_PROVIDER_LABELS: Dict[str, str] = {
    "jina": "Jina Reader",
    "tavily": "Tavily",
    "exa": "Exa",
    "parallel": "Parallel",
}

_DIRECT_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_DIRECT_TIMEOUT = 30


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
        return {"error": tr("webpage.api_key_missing")}

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
                return {"error": tr("webpage.api_request_failed", status_code=response.status_code)}

    except httpx.TimeoutException:
        return {"error": tr("webpage.timeout")}
    except httpx.RequestError as e:
        return {"error": tr("webpage.network_error", error=str(e))}
    except Exception as e:
        logger.error(f"网页提取异常: {e}")
        return {"error": tr("webpage.extract_error", error=str(e))}


def format_extract_results(results: Dict[str, Any]) -> str:
    """
    格式化提取结果为简洁版本

    Args:
        results: tavily_extract返回的结果

    Returns:
        格式化后的内容字符串
    """
    if "error" in results:
        return tr("webpage.format_failed", error=results["error"])

    if not results.get("results"):
        return tr("webpage.no_content")

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
            formatted_parts.append(f"- {failed.get('url', 'N/A')}: {failed.get('error', tr('webpage.unknown_error'))}")

    return "\n".join(formatted_parts)


# ============================================================
# 白名单直提
# ============================================================

def _normalize_domain(raw: Any) -> str:
    """把用户输入规范化为小写裸域名（容忍粘贴完整 URL / 前导点 / 路径）。"""
    if not isinstance(raw, str):
        return ""
    d = raw.strip().lower()
    if not d:
        return ""
    if "://" in d:
        d = _url_hostname(d) or d
    d = d.split("/")[0].split("?")[0].strip(".")
    if not re.fullmatch(r"[a-z0-9.-]+", d or "") or "." not in d:
        return ""
    return d


def _url_hostname(url: str) -> str:
    try:
        return (urlparse(str(url)).hostname or "").lower()
    except Exception:
        return ""


def resolve_direct_extract_config(personalization: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从 personalization 配置解析直提设置。

    Returns:
        {"enabled": bool, "domains": [...]} —— domains 已合并内置域名并规范化。
    """
    personalization = personalization or {}
    enabled = bool(personalization.get("webpage_direct_extract_enabled", True))
    extra = personalization.get("webpage_direct_extract_domains")
    if not isinstance(extra, list):
        extra = []
    domains: List[str] = []
    for item in list(BUILTIN_DIRECT_EXTRACT_DOMAINS) + extra:
        nd = _normalize_domain(item)
        if nd and nd not in domains:
            domains.append(nd)
    return {"enabled": enabled, "domains": domains}


def is_whitelisted_url(url: str, domains: List[str]) -> bool:
    """域名等于白名单条目或为其子域名即命中。"""
    host = _url_hostname(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


_GITHUB_BLOB_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/?#]+)/([^/?#]+)/blob/([^/?#]+)/([^?#]+?)(?:[?#].*)?$",
    re.IGNORECASE,
)


def parse_github_blob_url(url: str) -> Optional[Tuple[str, str, str, str]]:
    """解析 GitHub blob 页面 URL，返回 (owner, repo, branch, path)；非 blob 页返回 None。

    注意：branch 按单段处理（覆盖 main/master 等常见场景）；含斜杠的分支名
    会解析失败并自然降级到通用提取，不会报错。
    """
    m = _GITHUB_BLOB_RE.match(str(url).strip())
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4)


async def _fetch_jsdelivr_raw(client: httpx.AsyncClient, owner: str, repo: str, branch: str, path: str) -> Optional[str]:
    """经 jsDelivr CDN 拿 GitHub 文件原文（免费、无限速，替代被墙的 raw.githubusercontent.com）。"""
    cdn_url = f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}"
    try:
        resp = await client.get(cdn_url, timeout=_DIRECT_TIMEOUT)
        if resp.status_code == 200 and resp.text:
            return resp.text
        logger.info(f"jsDelivr 直链返回 {resp.status_code}: {cdn_url}")
    except Exception as e:
        logger.info(f"jsDelivr 直链失败 {cdn_url}: {e}")
    return None


async def _fetch_github_api_raw(client: httpx.AsyncClient, owner: str, repo: str, branch: str, path: str) -> Optional[str]:
    """经 GitHub 官方 contents API 拿文件原文（备用；匿名限 60 次/小时，>1MB 文件不返回内容）。"""
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    try:
        resp = await client.get(api_url, headers={"Accept": "application/vnd.github+json"}, timeout=_DIRECT_TIMEOUT)
        if resp.status_code != 200:
            logger.info(f"GitHub API 返回 {resp.status_code}: {api_url}")
            return None
        data = resp.json()
        if isinstance(data, dict) and data.get("encoding") == "base64" and data.get("content"):
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except Exception as e:
        logger.info(f"GitHub API 失败 {api_url}: {e}")
    return None


def _trafilatura_extract(html: str, url: str) -> Optional[str]:
    """trafilatura 通用正文提取（markdown 输出，保留标题/代码块结构）。"""
    if _trafilatura is None:
        return None
    try:
        text = _trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=False,
            include_images=False,
        )
        if text and text.strip():
            return text.strip()
    except Exception as e:
        logger.info(f"trafilatura 提取失败 {url}: {e}")
    return None


async def _direct_extract(client: httpx.AsyncClient, url: str) -> Tuple[Optional[str], str]:
    """白名单直提主流程。返回 (内容, method)；全部失败返回 (None, "")。"""
    blob = parse_github_blob_url(url)
    if blob:
        owner, repo, branch, path = blob
        content = await _fetch_jsdelivr_raw(client, owner, repo, branch, path)
        if content is not None:
            return content, "jsdelivr"
        content = await _fetch_github_api_raw(client, owner, repo, branch, path)
        if content is not None:
            return content, "github_api"
        # 直链均失败 → 继续走通用提取兜底

    if _trafilatura is not None:
        try:
            resp = await client.get(url, timeout=_DIRECT_TIMEOUT)
            if resp.status_code == 200 and resp.text:
                text = _trafilatura_extract(resp.text, url)
                if text:
                    return text, "trafilatura"
            else:
                logger.info(f"直提抓取返回 {resp.status_code}: {url}")
        except Exception as e:
            logger.info(f"直提抓取失败 {url}: {e}")
    return None, ""


def _format_single_result(url: str, content: str, method: Optional[str] = None) -> str:
    """格式化单条提取结果（沿用 🌐 骨架，附提取方式标注）。"""
    header = f"🌐 网页内容 ({len(content)} 字符)"
    if method:
        header += f" [{tr('webpage.method_label', method=tr(f'webpage.method_{method}'))}]"
    return "\n".join([
        header + ":",
        f"📍 URL: {url}",
        "=" * 50,
        content,
        "=" * 50,
    ])


async def jina_extract(url: str, api_key: str = "") -> Optional[str]:
    """Jina Reader 提取（可选提取商之一，默认；无 key 匿名限速可用）。

    GET https://r.jina.ai/<url>；无 key 匿名限速（约 20 RPM），带 key 提额。
    成功返回 markdown 文本，失败（含 429 限流）返回 None。
    """
    target = str(url).split("#", 1)[0]  # fragment 不由客户端发送，先剥离
    headers = {"Accept": "text/plain"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            resp = await client.get(f"https://r.jina.ai/{target}", headers=headers)
        if resp.status_code == 200 and resp.text and resp.text.strip():
            return resp.text.strip()
        logger.info(f"Jina Reader 返回 {resp.status_code}: {url}")
    except Exception as e:
        logger.info(f"Jina Reader 失败 {url}: {e}")
    return None


_EXA_MAX_CHARACTERS = 50000


async def exa_extract(url: str, api_key: str) -> Optional[str]:
    """Exa /contents 提取（可选提取商之一；按页计费 $1/1k pages，x-api-key 认证）。

    POST https://api.exa.ai/contents，text 视图返回干净正文。
    成功返回文本，失败返回 None。
    """
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.exa.ai/contents",
                json={"urls": [url], "text": {"maxCharacters": _EXA_MAX_CHARACTERS}},
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            logger.info(f"Exa contents 返回 {resp.status_code}: {url}")
            return None
        data = resp.json()
        for item in (data or {}).get("results") or []:
            if isinstance(item, dict):
                text = (item.get("text") or "").strip()
                if text:
                    return text
    except Exception as e:
        logger.info(f"Exa contents 失败 {url}: {e}")
    return None


_PARALLEL_MAX_CHARS = 50000


async def parallel_extract(url: str, api_key: str) -> Optional[str]:
    """Parallel /v1/extract 提取（可选提取商之一；按 URL 计费 $1/1k，600 次/分钟）。

    POST https://api.parallel.ai/v1/extract（x-api-key 认证）；GA 版 excerpts 总是返回，
    full_content 需经 advanced_settings.full_content 显式开启（官方 OpenAPI 契约）。
    HTTP 200 可能是部分成功：目标 URL 出现在 errors 而非 results 时视为失败。
    成功返回文本（优先 full_content，否则拼接 excerpts），失败返回 None。
    """
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.parallel.ai/v1/extract",
                json={
                    "urls": [url],
                    "advanced_settings": {
                        "full_content": {"max_chars_per_result": _PARALLEL_MAX_CHARS}
                    },
                },
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            logger.info(f"Parallel extract 返回 {resp.status_code}: {url}")
            return None
        data = resp.json()
        for item in (data or {}).get("results") or []:
            if not isinstance(item, dict):
                continue
            full = item.get("full_content")
            if isinstance(full, str) and full.strip():
                return full.strip()
            excerpts = item.get("excerpts")
            if isinstance(excerpts, list):
                text = "\n".join(str(e) for e in excerpts if e).strip()
                if text:
                    return text
        errors = (data or {}).get("errors")
        if errors:
            logger.info(f"Parallel extract 部分失败 {url}: {errors}")
    except Exception as e:
        logger.info(f"Parallel extract 失败 {url}: {e}")
    return None


def resolve_extract_provider_config(personalization: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从 personalization 解析网页提取商与凭证（UI 配置优先、环境变量兜底）。

    Returns:
        {"provider": "jina"|"tavily"|"exa"|"parallel", "api_key": str}
        jina 无需 key（匿名限速）；其余三家复用搜索的同源凭证解析。
    """
    personalization = personalization or {}
    name = str(personalization.get("webpage_extract_provider") or "").strip().lower()
    if name not in EXTRACT_PROVIDERS:
        name = DEFAULT_EXTRACT_PROVIDER
    if name == "jina":
        api_key = str(personalization.get("jina_api_key") or "").strip()
        if not api_key:
            try:
                import config as _config

                api_key = str(getattr(_config, "JINA_API_KEY", "") or "").strip()
            except Exception:
                api_key = ""
    else:
        from modules.search_providers import resolve_search_credential

        api_key = resolve_search_credential(name, personalization)
    return {
        "provider": name,
        "api_key": api_key,
    }


async def extract_single_url(
    url: str,
    extract_config: Optional[Dict[str, Any]] = None,
    extract_depth: str = "basic",
    direct_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """统一单 URL 提取：白名单直提（唯一自动回退层）→ 用户选定的提取商。

    Args:
        url: 目标 URL
        extract_config: resolve_extract_provider_config() 的返回；None 视为默认 Jina
        extract_depth: Tavily 提取深度（仅 provider=tavily 时生效）
        direct_config: resolve_direct_extract_config() 的返回；None 视为关闭直提

    Returns:
        {"success": bool, "url": str, "content": str, "method": str} 或
        {"success": False, "url": str, "error": str, "method": str}
    """
    direct_config = direct_config or {}
    extract_config = extract_config or {}
    provider = str(extract_config.get("provider") or "").strip().lower()
    if provider not in EXTRACT_PROVIDERS:
        provider = DEFAULT_EXTRACT_PROVIDER
    api_key = str(extract_config.get("api_key") or "")
    label = _EXTRACT_PROVIDER_LABELS.get(provider, provider)

    # 第一层（唯一自动回退）：白名单域名本机直提
    if direct_config.get("enabled") and is_whitelisted_url(url, direct_config.get("domains") or []):
        content, method = None, ""
        try:
            async with httpx.AsyncClient(headers=_DIRECT_REQUEST_HEADERS, follow_redirects=True) as client:
                content, method = await _direct_extract(client, url)
        except Exception as e:
            logger.info(f"白名单直提异常 {url}: {e}")
        if content is not None:
            return {"success": True, "url": url, "content": content, "method": method}
        logger.info(f"白名单直提未取到内容，转交选定的提取商: {url}")

    # 第二层：用户选定的提取商；失败即报错，不再向其他家回退
    if provider == "tavily":
        results = await tavily_extract(url, api_key, extract_depth, 1)
        if "error" in results:
            return {"success": False, "url": url, "error": results["error"], "method": "tavily"}
        for item in results.get("results") or []:
            raw = (item.get("raw_content") or "").strip()
            if raw:
                return {"success": True, "url": url, "content": raw, "method": "tavily"}
        return {"success": False, "url": url, "error": tr("webpage.no_content"), "method": "tavily"}

    # jina / exa / parallel 三家同构（jina 无需 key）
    if provider != "jina" and not api_key:
        return {
            "success": False,
            "url": url,
            "error": tr("webpage.provider_key_missing", provider=label),
            "method": provider,
        }
    extractor = {"jina": jina_extract, "exa": exa_extract, "parallel": parallel_extract}[provider]
    content = await extractor(url, api_key)
    if content is not None:
        return {"success": True, "url": url, "content": content, "method": provider}
    return {
        "success": False,
        "url": url,
        "error": tr("webpage.provider_failed", provider=label),
        "method": provider,
    }


async def extract_webpage_content(
    urls: Union[str, List[str]],
    extract_config: Optional[Dict[str, Any]] = None,
    extract_depth: str = "basic",
    max_urls: int = 1,
    direct_config: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """
    完整的网页内容提取流程（白名单直提 → 用户选定的提取商，失败不回退）

    Args:
        urls: 要提取的URL（字符串或列表）
        extract_config: 提取商配置（resolve_extract_provider_config 返回）；None=默认 Jina
        extract_depth: 提取深度 (basic/advanced，仅 provider=tavily 时生效)
        max_urls: 最大提取URL数量
        direct_config: 直提配置（resolve_direct_extract_config 返回）；None=关闭直提

    Returns:
        (完整内容, 完整内容) - 为了兼容性返回相同内容两份
    """
    if isinstance(urls, str):
        urls = [urls]
    urls = urls[:max_urls]

    formatted_parts: List[str] = []
    for url in urls:
        result = await extract_single_url(
            url, extract_config=extract_config, extract_depth=extract_depth, direct_config=direct_config
        )
        if result.get("success"):
            formatted_parts.append(_format_single_result(url, result["content"], result.get("method")))
        else:
            formatted_parts.append(tr("webpage.format_failed", error=result.get("error")))

    formatted_content = "\n".join(formatted_parts)
    return formatted_content, formatted_content
