"""Inline Citations：引用注册表、marker 解析与校验。

数据流：
  工具执行（web_search / extract_webpage）→ CitationRegistry.register_url()
  → 工具结果文本带 [src_xxx]（模型可见）
  → 模型输出 【cite:src_xxx】/【file:相对路径】 marker
  → assistant 消息落库前 finalize_message_citations() 校验 + 挂载 message.citations
  → 前端渲染 citation chip

设计要点：
- 网页来源用 cite: 前缀 + ID（src_<sha1(规范化 url) 前 10 位>），确定性、同 URL 天然去重；
- 文件来源用 file: 前缀 + 工作区相对路径（可带 #L120-148 / #p7 locator），不经过 registry；
- registry 是会话运行期的临时查找表，不落盘；历史消息靠 message.citations 自包含恢复。
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# marker 语法：【cite:src_xxx】/【file:AGENTS.md】/同前缀多来源逗号并列（中英文逗号均可）
CITATION_MARK_RE = re.compile(r"【(cite|file):([^】]+)】")

# locator 后缀：#L120 / #L120-148 / #L120-L148（GitHub 风格）/ #p7
_LOCATOR_RE = re.compile(r"^(?P<path>.*?)#(?:L(?P<line_start>\d+)(?:-L?(?P<line_end>\d+))?|p(?P<page>\d+))$", re.IGNORECASE)

_SRC_ID_RE = re.compile(r"^src_[0-9a-f]{10}x*$")

SNIPPET_MAX_CHARS = 300

# 文件 snippet 读取上限（前 64KB 足够覆盖 locator 行段与开头摘要）
_FILE_SNIPPET_READ_BYTES = 65536

# 图片扩展名：引用弹层渲染 <img> 而不是文本摘要
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif"}


def _read_file_snippet(target: Path, ref: Dict[str, Any]) -> Optional[str]:
    """读取文本文件内容片段作为引用预览：有 #L 定位时取对应行段，否则取开头。

    二进制文件（含空字节）返回 None。空白压缩为单行，限长 SNIPPET_MAX_CHARS。
    """
    try:
        with open(target, "rb") as f:
            raw = f.read(_FILE_SNIPPET_READ_BYTES)
    except Exception:
        return None
    if b"\x00" in raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    line_start = ref.get("line_start")
    if line_start:
        lines = text.splitlines()
        start = max(1, int(line_start))
        end = min(len(lines), int(ref.get("line_end") or start))
        snippet = "\n".join(lines[start - 1 : end]) if start <= len(lines) else ""
    else:
        snippet = text
    # 保留换行（弹层 pre-line 渲染），仅压缩行内空白、去空行
    snippet = "\n".join(ln.strip() for ln in snippet.splitlines() if ln.strip())
    return snippet[:SNIPPET_MAX_CHARS] or None


def normalize_url(url: str) -> str:
    """URL 规范化：小写 scheme/host、去末尾斜杠。用于同来源去重。"""
    url = (url or "").strip()
    try:
        p = urlparse(url)
        scheme = (p.scheme or "https").lower()
        netloc = p.netloc.lower()
        path = p.path.rstrip("/")
        query = f"?{p.query}" if p.query else ""
        return f"{scheme}://{netloc}{path}{query}"
    except Exception:
        return url


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def make_url_citation_id(url: str) -> str:
    digest = hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:10]
    return f"src_{digest}"


class CitationRegistry:
    """会话级网页来源注册表（运行期内存态，不落盘）。"""

    def __init__(self) -> None:
        self._by_id: Dict[str, Dict[str, Any]] = {}
        self._id_by_url: Dict[str, str] = {}

    def register_url(
        self,
        *,
        title: str = "",
        url: str = "",
        snippet: str = "",
        published_date: Optional[str] = None,
        source_tool: Optional[str] = None,
    ) -> Dict[str, Any]:
        """注册（或按规范化 URL 去重返回已有）一个网页来源 annotation。"""
        if not url:
            return {}
        norm = normalize_url(url)
        existing_id = self._id_by_url.get(norm)
        if existing_id is not None:
            return self._by_id[existing_id]

        cid = make_url_citation_id(url)
        # 哈希碰撞兜底（同一 URL 已命中则上面已返回；不同 URL 撞 hash 时加后缀）
        while cid in self._by_id and normalize_url(self._by_id[cid].get("url", "")) != norm:
            cid += "x"

        ann: Dict[str, Any] = {
            "id": cid,
            "type": "url_citation",
            "title": title or url,
            "url": url,
            "domain": domain_of(url),
            "snippet": (snippet or "")[:SNIPPET_MAX_CHARS],
        }
        if published_date:
            ann["published_date"] = published_date
        if source_tool:
            ann["source_tool"] = source_tool
        self._by_id[cid] = ann
        self._id_by_url[norm] = cid
        return ann

    def resolve(self, citation_id: str) -> Optional[Dict[str, Any]]:
        return self._by_id.get(citation_id)


def get_registry(terminal: Any) -> CitationRegistry:
    """取 terminal 实例上的会话级 registry（懒创建）。"""
    reg = getattr(terminal, "_citation_registry", None)
    if reg is None:
        reg = CitationRegistry()
        terminal._citation_registry = reg
    return reg


def parse_marker_content(content: str) -> List[str]:
    """拆分 marker 内容为引用 token 列表（支持中英文逗号分隔）。"""
    parts = re.split(r"[,，]", content or "")
    return [p.strip() for p in parts if p.strip()]


def extract_citation_refs(text: str) -> List[str]:
    """提取正文中全部完整 marker 的引用 token（保持出现顺序，去重）。"""
    refs: List[str] = []
    for m in CITATION_MARK_RE.finditer(text or ""):
        for token in parse_marker_content(m.group(2)):
            if token not in refs:
                refs.append(token)
    return refs


def _parse_file_ref(token: str) -> Optional[Dict[str, Any]]:
    """解析文件引用 token（裸相对路径，可带 #L120-148 / #p7 locator）。非法返回 None。"""
    body = token.strip()
    if not body:
        return None
    m = _LOCATOR_RE.match(body)
    if m:
        ref: Dict[str, Any] = {"path": m.group("path")}
        if m.group("page"):
            ref["page"] = int(m.group("page"))
        if m.group("line_start"):
            ref["line_start"] = int(m.group("line_start"))
            if m.group("line_end"):
                ref["line_end"] = int(m.group("line_end"))
        return ref
    return {"path": body}


def _build_file_annotation(ref: Dict[str, Any], token: str, workspace_root: str) -> Optional[Dict[str, Any]]:
    """校验文件引用：路径必须落在工作区内且文件存在；富化 file_name/size。

    annotation id 与 marker 内 token 完全一致，前端按 id 精确匹配。
    """
    # 注意不能用 lstrip("./")：它是字符集语义，会把 .astrion 这类隐藏目录的前导点吃掉
    rel_path = (ref.get("path") or "").strip()
    if rel_path.startswith("./"):
        rel_path = rel_path[2:]
    if not rel_path or rel_path.startswith("/"):
        return None
    try:
        root = Path(workspace_root).resolve()
        target = (root / rel_path).resolve()
        # 边界检查：必须在工作区内
        if root != target and root not in target.parents:
            return None
        if not target.is_file():
            return None
        stat = target.stat()
    except Exception:
        return None

    ann: Dict[str, Any] = {
        "id": token,
        "type": "file_citation",
        "file_path": rel_path,
        "file_name": target.name,
        "size": stat.st_size,
    }
    snippet: Optional[str] = None
    # 图片文件的 snippet 无意义（且 svg 这类文本型图片会把标记源码当摘要），
    # 前端弹层按扩展名直接渲染 <img>
    if target.suffix.lower() not in _IMAGE_EXTS:
        snippet = _read_file_snippet(target, ref)
    if snippet:
        ann["snippet"] = snippet
    if ref.get("page") is not None:
        ann["page"] = ref["page"]
    if ref.get("line_start") is not None:
        ann["line_start"] = ref["line_start"]
        if ref.get("line_end") is not None:
            ann["line_end"] = ref["line_end"]
    return ann


def finalize_message_citations(
    text: str,
    registry: Optional[CitationRegistry],
    workspace_root: str,
) -> Tuple[str, List[Dict[str, Any]]]:
    """assistant 消息落库前处理：剥离无效 marker，返回 (clean_text, used_annotations)。

    - src_xxx：registry 查得到才保留，否则剥离 marker；
    - file: 引用：路径在工作区内且文件存在才保留；
    - 一个 marker 里部分有效时，只保留有效 token 重建 marker；全部无效则整体移除。
    """
    if not text or ("【cite:" not in text and "【file:" not in text):
        return text, []

    used: List[Dict[str, Any]] = []
    seen_ids = set()

    def _keep(ann: Optional[Dict[str, Any]]) -> Optional[str]:
        if not ann:
            return None
        ann_id = ann.get("id")
        if ann_id and ann_id not in seen_ids:
            seen_ids.add(ann_id)
            used.append(ann)
        return ann_id

    def _replace(m: re.Match) -> str:
        kind = m.group(1)
        tokens = parse_marker_content(m.group(2))
        kept: List[str] = []
        for token in tokens:
            if kind == "cite":
                # 网页来源：src_xxx 且 registry 查得到才保留
                if not _SRC_ID_RE.match(token):
                    continue
                ann = registry.resolve(token) if registry else None
                if _keep(ann):
                    kept.append(token)
            else:
                # 文件来源：路径在工作区内且存在才保留
                ref = _parse_file_ref(token)
                if not ref:
                    continue
                ann = _build_file_annotation(ref, token, workspace_root)
                if _keep(ann):
                    kept.append(token)
        if not kept:
            return ""
        return "【" + kind + ":" + ",".join(kept) + "】"

    clean = CITATION_MARK_RE.sub(_replace, text)
    return clean, used
