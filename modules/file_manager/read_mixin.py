# modules/file_manager.py - 文件管理模块（添加行编辑功能）

import os
import shutil
from pathlib import Path
import re
from bisect import bisect_right
from typing import Any, Optional, Dict, List, Set, Tuple, TYPE_CHECKING
from datetime import datetime
try:
    from config import (
        MAX_FILE_SIZE,
        FORBIDDEN_PATHS,
        FORBIDDEN_ROOT_PATHS,
        OUTPUT_FORMATS,
        READ_TOOL_MAX_FILE_SIZE,
        PROJECT_MAX_STORAGE_BYTES,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
    )
except ImportError:  # 兼容全局环境中存在同名包的情况
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        MAX_FILE_SIZE,
        FORBIDDEN_PATHS,
        FORBIDDEN_ROOT_PATHS,
        OUTPUT_FORMATS,
        READ_TOOL_MAX_FILE_SIZE,
        PROJECT_MAX_STORAGE_BYTES,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
    )
from modules.container_file_proxy import ContainerFileProxy
from modules.host_sandbox_policy import get_macos_writable_paths, get_macos_readable_paths
from utils.logger import setup_logger
from modules.i18n import tr

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle

# 临时禁用长度检查
DISABLE_LENGTH_CHECK = True

logger = setup_logger(__name__)

class ReadMixin:
    """FileManager read mixin 能力 mixin。"""

    def _read_text_lines(
        self,
        full_path: Path,
        *,
        size_limit: Optional[int] = None,
        encoding: str = "utf-8",
    ) -> Dict:
        """读取UTF-8文本并返回行列表。"""
        try:
            file_size = full_path.stat().st_size
        except FileNotFoundError:
            return {"success": False, "error": tr("file_manager.file_not_found")}
        
        if size_limit and file_size > size_limit:
            return {
                "success": False,
                "error": tr(
                    "file_manager.file_too_large",
                    size=f"{file_size / 1024 / 1024:.2f}",
                    limit=f"{size_limit / 1024 / 1024}",
                )
            }
        
        try:
            with open(full_path, 'r', encoding=encoding) as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            return {
                "success": False,
                "error": tr("file_manager.not_utf8_text")
            }
        except Exception as e:
            return {"success": False, "error": tr("file_manager.read_failed", error=e)}
        
        content = "".join(lines)
        return {
            "success": True,
            "content": content,
            "lines": lines,
            "size": file_size
        }

    def read_file(self, path: str) -> Dict:
        """读取文件内容（兼容旧逻辑，限制为 MAX_FILE_SIZE）。"""
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        
        if not full_path.exists():
            return {"success": False, "error": tr("file_manager.file_not_found")}
        
        if not full_path.is_file():
            return {"success": False, "error": tr("file_manager.not_a_file")}
        ok, msg = self._ensure_host_access(full_path, "read")
        if not ok:
            return {"success": False, "error": msg}
        
        if self._use_container():
            relative_path = self._relative_path(full_path)
            result = self._container_call("read_file", {
                "path": relative_path,
                "size_limit": MAX_FILE_SIZE
            })
            return result

        result = self._read_text_lines(full_path, size_limit=MAX_FILE_SIZE)
        if not result["success"]:
            return result
        
        relative_path = self._relative_path(full_path)
        return {
            "success": True,
            "path": relative_path,
            "content": result["content"],
            "size": result["size"]
        }

    def read_text_segment(
        self,
        path: str,
        *,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        size_limit: Optional[int] = None
    ) -> Dict:
        """按行范围读取文本片段。"""
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        
        if not full_path.exists():
            return {"success": False, "error": tr("file_manager.file_not_found")}
        
        if not full_path.is_file():
            return {"success": False, "error": tr("file_manager.not_a_file")}
        ok, msg = self._ensure_host_access(full_path, "read")
        if not ok:
            return {"success": False, "error": msg}
        
        if self._use_container():
            relative_path = self._relative_path(full_path)
            result = self._container_call("read_text_segment", {
                "path": relative_path,
                "start_line": start_line,
                "end_line": end_line,
                "size_limit": size_limit or READ_TOOL_MAX_FILE_SIZE
            })
            return result

        if self._use_container():
            relative_path = self._relative_path(full_path)
            return self._container_call("search_text", {
                "path": relative_path,
                "query": query,
                "max_matches": max_matches,
                "context_before": context_before,
                "context_after": context_after,
                "case_sensitive": case_sensitive,
            })

        if self._use_container():
            relative_path = self._relative_path(full_path)
            return self._container_call("extract_segments", {
                "path": relative_path,
                "segments": segments,
                "size_limit": size_limit or READ_TOOL_MAX_FILE_SIZE
            })

        result = self._read_text_lines(
            full_path,
            size_limit=size_limit or READ_TOOL_MAX_FILE_SIZE
        )
        if not result["success"]:
            return result
        
        lines = result["lines"]
        total_lines = len(lines)
        if total_lines == 0:
            relative_path = self._relative_path(full_path)
            return {
                "success": True,
                "path": relative_path,
                "content": "",
                "size": result["size"],
                "line_start": 0,
                "line_end": 0,
                "total_lines": 0,
            }
        start = start_line if start_line and start_line > 0 else 1
        end = end_line if end_line and end_line >= start else total_lines
        if start > total_lines:
            return {"success": False, "error": tr("file_manager.start_line_out_of_file")}
        end = min(end, total_lines)
        
        selected_lines = lines[start - 1 : end]
        content = "".join(selected_lines)
        
        relative_path = self._relative_path(full_path)
        return {
            "success": True,
            "path": relative_path,
            "content": content,
            "size": result["size"],
            "line_start": start,
            "line_end": end,
            "total_lines": total_lines
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
        size_limit: Optional[int] = None
    ) -> Dict:
        """在文件中搜索关键词，返回合并后的窗口。"""
        if not query:
            return {"success": False, "error": tr("file_manager.missing_search_query")}
        
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        
        if not full_path.exists():
            return {"success": False, "error": tr("file_manager.file_not_found")}
        
        if not full_path.is_file():
            return {"success": False, "error": tr("file_manager.not_a_file")}
        ok, msg = self._ensure_host_access(full_path, "read")
        if not ok:
            return {"success": False, "error": msg}
        
        result = self._read_text_lines(
            full_path,
            size_limit=size_limit or READ_TOOL_MAX_FILE_SIZE
        )
        if not result["success"]:
            return result
        
        lines = result["lines"]
        total_lines = len(lines)
        matches = []
        query_text = query if case_sensitive else query.lower()
        
        def contains(haystack: str) -> bool:
            target = haystack if case_sensitive else haystack.lower()
            return query_text in target
        
        for idx, line in enumerate(lines, start=1):
            if contains(line):
                window_start = max(1, idx - context_before)
                window_end = min(total_lines, idx + context_after)
                
                if matches and window_start <= matches[-1]["line_end"]:
                    matches[-1]["line_end"] = max(matches[-1]["line_end"], window_end)
                    matches[-1]["hits"].append(idx)
                else:
                    if len(matches) >= max_matches:
                        break
                    matches.append({
                        "line_start": window_start,
                        "line_end": window_end,
                        "hits": [idx]
                    })
        
        relative_path = self._relative_path(full_path)
        for window in matches:
            snippet_lines = lines[window["line_start"] - 1 : window["line_end"]]
            window["snippet"] = "".join(snippet_lines)
        
        return {
            "success": True,
            "path": relative_path,
            "size": result["size"],
            "total_lines": total_lines,
            "matches": matches
        }

    def extract_segments(
        self,
        path: str,
        segments: List[Dict],
        *,
        size_limit: Optional[int] = None
    ) -> Dict:
        """根据多个行区间提取内容。"""
        if not segments:
            return {"success": False, "error": tr("file_manager.missing_segments")}
        
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        
        if not full_path.exists():
            return {"success": False, "error": tr("file_manager.file_not_found")}
        
        if not full_path.is_file():
            return {"success": False, "error": tr("file_manager.not_a_file")}
        ok, msg = self._ensure_host_access(full_path, "read")
        if not ok:
            return {"success": False, "error": msg}
        
        result = self._read_text_lines(
            full_path,
            size_limit=size_limit or READ_TOOL_MAX_FILE_SIZE
        )
        if not result["success"]:
            return result
        
        lines = result["lines"]
        total_lines = len(lines)
        extracted = []
        
        for item in segments:
            if not isinstance(item, dict):
                return {"success": False, "error": tr("file_manager.segments_items_must_be_objects")}
            start_line = item.get("start_line")
            end_line = item.get("end_line")
            label = item.get("label")
            if start_line is None or end_line is None:
                return {"success": False, "error": tr("file_manager.segments_need_line_bounds")}
            if start_line <= 0 or end_line < start_line:
                return {"success": False, "error": tr("file_manager.segment_range_invalid")}
            if start_line > total_lines:
                return {"success": False, "error": tr("file_manager.segment_start_exceeds", line=start_line)}
            end_line = min(end_line, total_lines)
            snippet = "".join(lines[start_line - 1 : end_line])
            extracted.append({
                "label": label,
                "line_start": start_line,
                "line_end": end_line,
                "content": snippet
            })
        
        relative_path = self._relative_path(full_path)
        return {
            "success": True,
            "path": relative_path,
            "size": result["size"],
            "total_lines": total_lines,
            "segments": extracted
        }
