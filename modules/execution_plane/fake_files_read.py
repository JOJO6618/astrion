"""FakeFileSystem 的读协议 mixin：内容 / 行片段 / 搜索 / 多段 / 二进制。

单独成文件是为了让每个模块保持在可审阅的体量内；本 mixin 不自带状态，
依赖宿主 ``FakeFileSystem`` 提供的 ``_normalize/_is_file/_text/_lines`` 等内部工具，
因此**不能**独立实例化，也不应绕过宿主直接调用本地磁盘 API。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class FakeFileReadMixin:
    """读路径：只消费内存记录，不做任何本地 stat/open。"""

    # ------------------------------------------------------------------
    # 内容读取
    # ------------------------------------------------------------------
    def read_file(self, path: str) -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if not self._is_file(key):
            return {"success": False, "error": "file not found"}
        text = self._text(key)
        if text is None:
            return {"success": False, "error": "file is not valid UTF-8 text"}
        return {
            "success": True,
            "path": key,
            "content": text,
            "size": len(dict.__getitem__(self, key)),
        }

    def read_text_segment(
        self,
        path: str,
        *,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        size_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if not self._is_file(key):
            return {"success": False, "error": "file not found"}
        raw = dict.__getitem__(self, key)
        if size_limit and len(raw) > size_limit:
            return {"success": False, "error": "file too large"}
        lines = self._lines(key)
        if self._text(key) is None:
            return {"success": False, "error": "file is not valid UTF-8 text"}
        total = len(lines)
        start = 1 if start_line is None else int(start_line)
        end = total if end_line is None else int(end_line)
        if start <= 0 or end < start:
            return {"success": False, "error": "invalid line range"}
        if total == 0 or start > total:
            return {"success": False, "error": "start line exceeds file length"}
        end = min(end, total)
        content = "".join(lines[start - 1:end])
        return {
            "success": True,
            "path": key,
            "content": content,
            "size": len(raw),
            "line_start": start,
            "line_end": end,
            "total_lines": total,
        }

    def search_text(
        self,
        path: str,
        *,
        query: str,
        max_matches: int,
        context_before: int,
        context_after: int,
        case_sensitive: bool = False,
        size_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if not query:
            return {"success": False, "error": "query is required"}
        if not self._is_file(key):
            return {"success": False, "error": "file not found"}
        raw = dict.__getitem__(self, key)
        if size_limit and len(raw) > size_limit:
            return {"success": False, "error": "file too large"}
        text = self._text(key)
        if text is None:
            return {"success": False, "error": "file is not valid UTF-8 text"}
        lines = text.splitlines(keepends=True)
        total = len(lines)
        needle = query if case_sensitive else query.lower()

        def contains(haystack: str) -> bool:
            return needle in (haystack if case_sensitive else haystack.lower())

        matches: List[Dict[str, Any]] = []
        for idx, line in enumerate(lines, start=1):
            if not contains(line):
                continue
            window_start = max(1, idx - context_before)
            window_end = min(total, idx + context_after)
            if matches and window_start <= matches[-1]["line_end"]:
                matches[-1]["line_end"] = max(matches[-1]["line_end"], window_end)
                matches[-1]["hits"].append(idx)
            else:
                if len(matches) >= max_matches:
                    break
                matches.append({
                    "line_start": window_start,
                    "line_end": window_end,
                    "hits": [idx],
                })
        for window in matches:
            window["snippet"] = "".join(lines[window["line_start"] - 1:window["line_end"]])
        return {
            "success": True,
            "path": key,
            "size": len(raw),
            "total_lines": total,
            "matches": matches,
        }

    def extract_segments(
        self,
        path: str,
        segments: List[Dict[str, Any]],
        *,
        size_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if not segments:
            return {"success": False, "error": "segments are required"}
        if not self._is_file(key):
            return {"success": False, "error": "file not found"}
        raw = dict.__getitem__(self, key)
        if size_limit and len(raw) > size_limit:
            return {"success": False, "error": "file too large"}
        if self._text(key) is None:
            return {"success": False, "error": "file is not valid UTF-8 text"}
        lines = self._lines(key)
        total = len(lines)
        extracted: List[Dict[str, Any]] = []
        for item in segments:
            if not isinstance(item, dict):
                return {"success": False, "error": "segment items must be objects"}
            start_line = item.get("start_line")
            end_line = item.get("end_line")
            if start_line is None or end_line is None:
                return {"success": False, "error": "segment needs line bounds"}
            start_line = int(start_line)
            end_line = int(end_line)
            if start_line <= 0 or end_line < start_line:
                return {"success": False, "error": "invalid segment range"}
            if start_line > total:
                return {"success": False, "error": "segment start exceeds file length"}
            end_line = min(end_line, total)
            extracted.append({
                "label": item.get("label"),
                "line_start": start_line,
                "line_end": end_line,
                "content": "".join(lines[start_line - 1:end_line]),
            })
        return {
            "success": True,
            "path": key,
            "size": len(raw),
            "total_lines": total,
            "segments": extracted,
        }

    def read_binary(self, path: str) -> bytes:
        """返回原始 bytes；不存在/越界抛异常（与 LocalFileSystem 语义一致）。"""
        key, error = self._normalize(path)
        if error:
            raise PermissionError(error)
        if not self._is_file(key):
            raise FileNotFoundError(path)
        return dict.__getitem__(self, key)



__all__ = ["FakeFileReadMixin"]
