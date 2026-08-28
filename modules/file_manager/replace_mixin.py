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

class ReplaceMixin:
    """FileManager replace mixin 能力 mixin。"""

    def replace_in_file(self, path: str, old_text: str, new_text: str, replace_all: bool = False) -> Dict:
        """替换文件中的内容"""
        # 先读取文件
        result = self.read_file(path)
        if not result["success"]:
            return result
        
        content = result["content"]
        
        # === 新增：替换操作的安全检查 ===
        if old_text and len(old_text) > 9999999999:
            return {
                "success": False,
                "error": tr("file_manager.replace_old_text_too_long"),
                "suggestion": "请拆分内容或使用 edit_file/逐块写入完成修改"
            }
        
        if new_text and len(new_text) > 9999999999:
            return {
                "success": False,
                "error": tr("file_manager.replace_new_text_too_long"),
                "suggestion": "请将大内容分成多个小的替换操作"
            }
        short_old_text_notice = bool(old_text and len(old_text.splitlines()) < 3)

        # 检查是否包含要替换的内容
        if old_text and old_text not in content:
            return {"success": False, "error": tr("file_manager.replace_not_found")}

        matched_lines = self._find_match_line_numbers(content, old_text) if old_text else []
        found_count = len(matched_lines) if old_text else 0

        # 替换内容
        if old_text:
            if replace_all:
                count = found_count
                new_content = content.replace(old_text, new_text)
            else:
                count = 1
                new_content = content.replace(old_text, new_text, 1)
        else:
            # 空文件直接写入新内容
            new_content = new_text
            count = 1
        
        # 写回文件
        result = self.write_file(path, new_content)
        if result["success"]:
            result["replacements"] = count
            message_parts: List[str] = []
            if old_text:
                result["found_matches"] = found_count
                result["matched_lines"] = matched_lines
                if found_count > 1:
                    line_text = ",".join(str(line_no) for line_no in matched_lines)
                    message_parts.append(tr(
                        "file_manager.replace_found_report",
                        found=found_count,
                        lines=line_text,
                        count=count,
                    ))
            if short_old_text_notice:
                message_parts.insert(
                    0,
                    tr("file_manager.replace_short_old_notice")
                )
            if message_parts:
                result["message"] = "；".join(message_parts)
            print(f"{OUTPUT_FORMATS['file']} 替换了 {count} 处内容")

        return result

    def replace_many_in_file(self, path: str, replacements: List[Dict[str, Any]]) -> Dict:
        """按顺序执行多组精确字符串替换；任意一组失败时不写入文件。"""
        result = self.read_file(path)
        if not result["success"]:
            return result

        if not isinstance(replacements, list) or not replacements:
            return {"success": False, "error": tr("file_manager.replacements_required")}

        if len(replacements) > 100:
            return {
                "success": False,
                "error": tr("file_manager.replacements_too_many"),
                "suggestion": "请拆分为多次 edit_file 调用"
            }

        original_content = result["content"]
        current_content = original_content
        details: List[Dict[str, Any]] = []
        failed_details: List[Dict[str, Any]] = []
        total_replacements = 0
        total_found_matches = 0
        short_old_text_indices: List[int] = []

        for zero_based_index, item in enumerate(replacements):
            index = zero_based_index + 1
            detail: Dict[str, Any] = {
                "index": index,
                "status": "pending",
                "replace_all": False,
                "found_matches": 0,
                "replacements": 0,
                "matched_lines": [],
            }

            if not isinstance(item, dict):
                detail.update({
                    "status": "error",
                    "reason": "替换项必须是对象，包含 old_string 和 new_string"
                })
                failed_details.append(detail.copy())
                details.append(detail)
                break

            old_text = item.get("old_string")
            new_text = item.get("new_string")
            replace_all = item.get("replace_all", False)
            detail["replace_all"] = replace_all

            if old_text is None or new_text is None:
                detail.update({
                    "status": "error",
                    "reason": "缺少 old_string/new_string"
                })
                failed_details.append(detail.copy())
                details.append(detail)
                break
            if not isinstance(old_text, str) or not isinstance(new_text, str):
                detail.update({
                    "status": "error",
                    "reason": "old_string/new_string 必须是字符串"
                })
                failed_details.append(detail.copy())
                details.append(detail)
                break
            if not isinstance(replace_all, bool):
                detail.update({
                    "status": "error",
                    "reason": "replace_all 必须是 true 或 false；不提供时默认为 false"
                })
                failed_details.append(detail.copy())
                details.append(detail)
                break
            if old_text == new_text:
                detail.update({
                    "status": "error",
                    "reason": "old_string 与 new_string 相同，无法执行替换"
                })
                failed_details.append(detail.copy())
                details.append(detail)
                break
            if not old_text:
                detail.update({
                    "status": "error",
                    "reason": "old_string 不能为空，请从 read_file 内容中精确复制"
                })
                failed_details.append(detail.copy())
                details.append(detail)
                break

            if len(old_text) > 9999999999:
                detail.update({
                    "status": "error",
                    "reason": "要替换的文本过长，可能导致性能问题"
                })
                failed_details.append(detail.copy())
                details.append(detail)
                break
            if len(new_text) > 9999999999:
                detail.update({
                    "status": "error",
                    "reason": "替换的新文本过长，建议分块处理"
                })
                failed_details.append(detail.copy())
                details.append(detail)
                break

            if len(old_text.splitlines()) < 3:
                short_old_text_indices.append(index)

            matched_lines = self._find_match_line_numbers(current_content, old_text)
            found_count = len(matched_lines)
            if found_count == 0:
                detail.update({
                    "status": "not_found",
                    "reason": "未找到要替换的内容"
                })
                failed_details.append(detail.copy())
                details.append(detail)
                break

            replacement_count = found_count if replace_all else 1
            contexts = self._find_match_contexts(
                current_content,
                old_text,
                max_matches=replacement_count
            )

            if replace_all:
                current_content = current_content.replace(old_text, new_text)
            else:
                current_content = current_content.replace(old_text, new_text, 1)

            detail.update({
                "status": "success",
                "found_matches": found_count,
                "replacements": replacement_count,
                "matched_lines": matched_lines,
                "contexts": contexts,
                "old_string": old_text,
                "new_string": new_text,
                "old_lines": old_text.splitlines(),
                "new_lines": new_text.splitlines(),
                "old_line_count": len(old_text.splitlines()),
                "new_line_count": len(new_text.splitlines()),
            })
            details.append(detail)
            total_found_matches += found_count
            total_replacements += replacement_count

        if failed_details:
            return {
                "success": False,
                "path": result.get("path") or path,
                "error": tr(
                    "file_manager.replace_group_failed",
                    index=failed_details[0].get("index"),
                    reason=failed_details[0].get("reason"),
                ),
                "failed_replacement_index": failed_details[0].get("index"),
                "failed_details": failed_details,
                "details": details,
                "write_performed": False,
                "completed_replacements": len([item for item in details if item.get("status") == "success"]),
            }

        write_result = self.write_file(path, current_content)
        if write_result["success"]:
            write_result["replacements"] = total_replacements
            write_result["found_matches"] = total_found_matches
            write_result["replacement_groups"] = len(replacements)
            write_result["details"] = details
            write_result["write_performed"] = True
            message_parts: List[str] = [tr(
                "file_manager.replace_many_summary",
                groups=len(replacements),
                replacements=total_replacements,
            )]
            if short_old_text_indices:
                indices_text = ",".join(str(item) for item in short_old_text_indices)
                message_parts.append(tr("file_manager.replace_many_short_notice", indices=indices_text))
            write_result["message"] = "；".join(message_parts)
            print(f"{OUTPUT_FORMATS['file']} 批量替换了 {total_replacements} 处内容")
        return write_result

    @staticmethod
    def _find_match_line_numbers(content: str, target: str) -> List[int]:
        """返回 target 在 content 中每个匹配起始位置对应的行号（1-based）。"""
        if not target:
            return []
        newline_positions = [idx for idx, ch in enumerate(content) if ch == "\n"]
        line_numbers: List[int] = []
        search_start = 0
        target_len = len(target)

        while True:
            idx = content.find(target, search_start)
            if idx < 0:
                break
            line_no = bisect_right(newline_positions, idx) + 1
            line_numbers.append(line_no)
            search_start = idx + max(1, target_len)

        return line_numbers

    @staticmethod
    def _find_match_contexts(content: str, target: str, max_matches: Optional[int] = None) -> List[Dict[str, Any]]:
        """返回每个实际替换匹配位置的上下文行，仅用于结果 metadata 展示。"""
        if not target:
            return []

        lines = content.splitlines()
        newline_positions = [idx for idx, ch in enumerate(content) if ch == "\n"]
        contexts: List[Dict[str, Any]] = []
        search_start = 0
        target_len = len(target)
        old_line_count = max(1, len(target.splitlines()))

        while True:
            if max_matches is not None and len(contexts) >= max_matches:
                break
            idx = content.find(target, search_start)
            if idx < 0:
                break

            start_line = bisect_right(newline_positions, idx) + 1
            end_line = start_line + old_line_count - 1
            before_start = max(1, start_line - 2)
            before_lines = [
                {"line": line_no, "text": lines[line_no - 1]}
                for line_no in range(before_start, start_line)
                if 0 <= line_no - 1 < len(lines)
            ]
            after_end = min(len(lines), end_line + 2)
            after_lines = [
                {"line": line_no, "text": lines[line_no - 1]}
                for line_no in range(end_line + 1, after_end + 1)
                if 0 <= line_no - 1 < len(lines)
            ]
            contexts.append({
                "old_start_line": start_line,
                "old_end_line": end_line,
                "before_lines": before_lines,
                "after_lines": after_lines,
            })
            search_start = idx + max(1, target_len)

        return contexts
