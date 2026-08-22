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

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle

# 临时禁用长度检查
DISABLE_LENGTH_CHECK = True

logger = setup_logger(__name__)

class PatchMixin:
    """FileManager patch mixin 能力 mixin。"""

    def _parse_diff_patch(self, patch_text: str) -> Dict:
        """解析统一diff格式的补丁，转换为 apply_modify_blocks 所需的块结构。"""
        if not patch_text or "*** Begin Patch" not in patch_text or "*** End Patch" not in patch_text:
            return {
                "success": False,
                "error": "补丁格式错误：缺少 *** Begin Patch / *** End Patch 标记。"
            }

        start = patch_text.find("*** Begin Patch")
        end = patch_text.rfind("*** End Patch")
        if end <= start:
            return {
                "success": False,
                "error": "补丁格式错误：结束标记位置异常。"
            }

        body = patch_text[start + len("*** Begin Patch"):end]
        lines = body.splitlines(True)  # 保留换行符，便于逐字匹配

        blocks: List[Dict] = []
        current_block: Optional[Dict] = None
        auto_index = 1

        id_pattern = re.compile(r"\[id:\s*(\d+)\]", re.IGNORECASE)

        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped and current_block is None:
                continue

            if stripped.startswith("@@"):
                if current_block:
                    if not current_block["lines"]:
                        return {
                            "success": False,
                            "error": f"补丁块缺少内容：{current_block.get('header', '').strip()}"
                        }
                    blocks.append(current_block)

                header = stripped
                id_match = id_pattern.search(header)
                block_id: Optional[int] = None
                if id_match:
                    try:
                        block_id = int(id_match.group(1))
                    except ValueError:
                        return {
                            "success": False,
                            "error": f"补丁块编号必须是整数：{header}"
                        }

                current_block = {"id": block_id, "header": header, "lines": []}
                continue

            if current_block is None:
                if stripped:
                    return {
                        "success": False,
                        "error": "补丁格式错误：在检测到第一个 @@ 块之前出现内容。"
                    }
                continue

            if raw_line.startswith("\\ No newline at end of file"):
                continue

            current_block["lines"].append(raw_line)

        if current_block:
            if not current_block["lines"]:
                return {
                    "success": False,
                    "error": f"补丁块缺少内容：{current_block.get('header', '').strip()}"
                }
            blocks.append(current_block)

        if not blocks:
            return {
                "success": False,
                "error": "补丁格式错误：未检测到任何 @@ [id:n] 块。"
            }

        parsed_blocks: List[Dict] = []
        used_indices: Set[int] = set()

        for block in blocks:
            idx = block["id"]
            if idx is None:
                while auto_index in used_indices:
                    auto_index += 1
                idx = auto_index
                auto_index += 1
            elif idx in used_indices:
                while idx in used_indices:
                    idx += 1
                auto_index = max(auto_index, idx + 1)

            used_indices.add(idx)

            old_lines: List[str] = []
            new_lines: List[str] = []
            has_anchor = False
            has_content = False

            for line in block["lines"]:
                if not line:
                    continue
                prefix = line[0]
                if prefix == ' ':
                    has_anchor = True
                    old_lines.append(line[1:])
                    new_lines.append(line[1:])
                    has_content = True
                elif prefix == '-':
                    has_anchor = True
                    old_lines.append(line[1:])
                    has_content = True
                elif prefix == '+':
                    new_lines.append(line[1:])
                    has_content = True
                else:
                    # 容忍空白符或意外格式，直接作为上下文
                    old_lines.append(line)
                    new_lines.append(line)
                    has_anchor = True
                    has_content = True

            if not has_content:
                return {
                    "success": False,
                    "error": f"补丁块 {idx} 未包含任何 + / - / 上下文行。"
                }

            append_only = False
            if not has_anchor:
                append_only = True

            old_text = "".join(old_lines)
            new_text = "".join(new_lines)
            raw_patch = f"{block['header']}\n{''.join(block['lines'])}"

            parsed_blocks.append({
                "index": idx,
                "old": old_text,
                "new": new_text,
                "append_only": append_only,
                "raw_patch": raw_patch
            })

        return {
            "success": True,
            "blocks": parsed_blocks
        }

    def apply_diff_patch(self, path: str, patch_text: str) -> Dict:
        """解析统一diff并写入文件，支持多块依次执行。"""
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}

        parse_result = self._parse_diff_patch(patch_text)
        if not parse_result.get("success"):
            return parse_result

        blocks = parse_result.get("blocks") or []
        if not blocks:
            return {
                "success": False,
                "error": "未检测到有效的补丁块。"
            }

        relative_path = str(self._relative_path(full_path))

        parsed_blocks: List[Dict] = blocks
        block_lookup: Dict[int, Dict] = {block["index"]: block for block in parsed_blocks}

        def attach_block_context(entries: Optional[List[Dict]]):
            if not entries:
                return
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                idx = entry.get("index")
                if idx is None:
                    continue
                block_info = block_lookup.get(idx)
                if not block_info:
                    continue
                patch_text = block_info.get("raw_patch")
                if patch_text:
                    entry.setdefault("block_patch", patch_text)
                if "old_text" not in entry:
                    entry["old_text"] = block_info.get("old")
                if "new_text" not in entry:
                    entry["new_text"] = block_info.get("new")
                entry.setdefault("append_only", block_info.get("append_only", False))

        append_only_blocks = [b for b in parsed_blocks if b.get("append_only")]
        modify_blocks = [
            {"index": b["index"], "old": b["old"], "new": b["new"]}
            for b in parsed_blocks
            if not b.get("append_only")
        ]

        apply_result = {"results": []}
        completed_indices: List[int] = []
        failed_entries: List[Dict] = []
        write_error = None

        if modify_blocks:
            modify_result = self.apply_modify_blocks(path, modify_blocks)
            apply_result.update(modify_result)
            completed_indices.extend(modify_result.get("completed", []))
            failed_entries.extend(modify_result.get("failed", []))
            write_error = modify_result.get("error")
        else:
            apply_result.update({
                "success": True,
                "completed": [],
                "failed": [],
                "results": [],
                "write_performed": False,
                "error": None
            })

        results_blocks = apply_result.get("results", []).copy()
        append_results: List[Dict] = []
        append_bytes = 0
        append_lines_total = 0
        append_success = True

        if append_only_blocks:
            try:
                with open(full_path, 'a', encoding='utf-8') as f:
                    for block in append_only_blocks:
                        chunk = block.get("new", "")
                        if not chunk:
                            append_results.append({
                                "index": block["index"],
                                "status": "failed",
                                "reason": "追加块为空"
                            })
                            failed_entries.append({
                                "index": block["index"],
                                "reason": "追加块为空"
                            })
                            append_success = False
                            continue
                        f.write(chunk)
                        added_lines = chunk.count('\n')
                        if chunk and not chunk.endswith('\n'):
                            added_lines += 1
                        append_lines_total += added_lines
                        append_bytes += len(chunk.encode('utf-8'))
                        append_results.append({
                            "index": block["index"],
                            "status": "success",
                            "removed_lines": 0,
                            "added_lines": added_lines
                        })
                        completed_indices.append(block["index"])
            except Exception as e:
                append_success = False
                write_error = f"追加写入失败: {e}"
                append_results.append({
                    "index": append_only_blocks[-1]["index"],
                    "status": "failed",
                    "reason": str(e)
                })
                failed_entries.append({
                    "index": append_only_blocks[-1]["index"],
                    "reason": str(e)
                })

        attach_block_context(failed_entries)

        total_blocks = len(parsed_blocks)
        completed_unique = sorted(set(completed_indices))

        summary_parts = [
            f"向 {relative_path} 应用 {total_blocks} 个补丁块",
            f"成功 {len(completed_unique)} 个",
            f"失败 {len(failed_entries)} 个"
        ]
        if append_only_blocks:
            summary_parts.append(f"追加 {len(append_only_blocks)} 块，写入 {append_lines_total} 行（{append_bytes} 字节）")
        if write_error:
            summary_parts.append(write_error)
        summary = "，".join(summary_parts)

        results_blocks.extend(append_results)

        apply_result["results"] = results_blocks
        apply_result["blocks"] = results_blocks
        apply_result["path"] = relative_path
        apply_result["total_blocks"] = total_blocks
        apply_result["summary"] = summary
        apply_result["message"] = summary
        apply_result["completed"] = completed_unique
        apply_result["failed"] = failed_entries
        apply_result["append_bytes"] = append_bytes
        apply_result["append_blocks"] = len(append_only_blocks)
        apply_result["success"] = (
            append_success
            and apply_result.get("success", True)
            and not failed_entries
            and not write_error
        )
        apply_result["error"] = write_error
        return apply_result

    def apply_modify_blocks(self, path: str, blocks: List[Dict]) -> Dict:
        """
        应用批量替换块
        
        Args:
            path: 目标文件路径
            blocks: [{"index": int, "old": str, "new": str}]
        """
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        
        if not full_path.exists():
            return {"success": False, "error": "文件不存在"}
        
        if not full_path.is_file():
            return {"success": False, "error": "不是文件"}
        
        try:
            relative_path = self._relative_path(full_path)
            if self._use_container():
                return self._container_call("apply_modify_blocks", {
                    "path": relative_path,
                    "blocks": blocks
                })

            with open(full_path, 'r', encoding='utf-8') as f:
                original_content = f.read()
        except Exception as e:
            return {"success": False, "error": f"读取文件失败: {e}"}
        
        current_content = original_content
        results: List[Dict] = []
        completed_indices: List[int] = []
        failed_details: List[Dict] = []
        write_error = None
        
        for block in blocks:
            index = block.get("index")
            old_text = block.get("old", "")
            new_text = block.get("new", "")
            
            block_result = {
                "index": index,
                "status": "pending",
                "removed_lines": 0,
                "added_lines": 0,
                "reason": None,
                "hint": None
            }
            
            if old_text is None or new_text is None:
                block_result["status"] = "error"
                block_result["reason"] = "缺少 OLD 或 NEW 内容"
                block_result["hint"] = "请确保补丁包含成对的 OLD/NEW 段落。"
                failed_details.append({"index": index, "reason": "缺少 OLD/NEW 标记"})
                results.append(block_result)
                continue
            
            # 统一换行符，避免 CRLF 与 LF 不一致导致匹配失败
            old_text = old_text.replace('\r\n', '\n')
            new_text = new_text.replace('\r\n', '\n')
            
            if not old_text:
                block_result["status"] = "error"
                block_result["reason"] = "OLD 内容不能为空"
                block_result["hint"] = "请确认要替换的原文是否准确复制；若多次失败，可改用 terminal_snapshot 查证或使用终端命令/Python 小脚本进行精确替换。"
                failed_details.append({"index": index, "reason": "OLD 内容为空"})
                results.append(block_result)
                continue
            
            position = current_content.find(old_text)
            if position == -1:
                block_result["status"] = "not_found"
                block_result["reason"] = "未找到匹配的原文，请确认是否完全复制"
                block_result["hint"] = "请先用 terminal_snapshot 或 grep -n 校验原文；若仍失败，可在说明后改用 run_command/python 进行局部修改。"
                failed_details.append({"index": index, "reason": "未找到匹配的原文"})
                results.append(block_result)
                continue
            
            current_content = (
                current_content[:position] +
                new_text +
                current_content[position + len(old_text):]
            )
            
            removed_lines = old_text.count('\n')
            added_lines = new_text.count('\n')
            if old_text and not old_text.endswith('\n'):
                removed_lines += 1
            if new_text and not new_text.endswith('\n'):
                added_lines += 1
            
            block_result.update({
                "status": "success",
                "removed_lines": removed_lines if old_text else 0,
                "added_lines": added_lines if new_text else 0
            })
            completed_indices.append(index)
            results.append(block_result)
        
        write_performed = False
        if completed_indices:
            try:
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(current_content)
                write_performed = True
            except Exception as e:
                write_error = f"写入文件失败: {e}"
                # 写入失败时恢复原始内容
                try:
                    with open(full_path, 'w', encoding='utf-8') as f:
                        f.write(original_content)
                except Exception:
                    pass
        
        success = bool(completed_indices) and not failed_details and write_error is None
        
        return {
            "success": success,
            "completed": completed_indices,
            "failed": failed_details,
            "results": results,
            "write_performed": write_performed,
            "error": write_error
        }
