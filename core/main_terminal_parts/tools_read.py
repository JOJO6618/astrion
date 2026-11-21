import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from config import (
        OUTPUT_FORMATS, DATA_DIR, PROMPTS_DIR, NEED_CONFIRMATION,
        MAX_TERMINALS, TERMINAL_BUFFER_SIZE, TERMINAL_DISPLAY_SIZE,
        MAX_READ_FILE_CHARS, READ_TOOL_DEFAULT_MAX_CHARS,
        READ_TOOL_DEFAULT_CONTEXT_BEFORE, READ_TOOL_DEFAULT_CONTEXT_AFTER,
        READ_TOOL_MAX_CONTEXT_BEFORE, READ_TOOL_MAX_CONTEXT_AFTER,
        READ_TOOL_DEFAULT_MAX_MATCHES, READ_TOOL_MAX_MATCHES,
        READ_TOOL_MAX_FILE_SIZE,
        PROJECT_MAX_STORAGE_MB,
        CUSTOM_TOOLS_ENABLED,
        WORKSPACE_SKILLS_DIRNAME,
        PROJECT_AGENTS_SKILLS_DIRNAME,
    )
except ImportError:
    import sys
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS, DATA_DIR, PROMPTS_DIR, NEED_CONFIRMATION,
        MAX_TERMINALS, TERMINAL_BUFFER_SIZE, TERMINAL_DISPLAY_SIZE,
        MAX_READ_FILE_CHARS, READ_TOOL_DEFAULT_MAX_CHARS,
        READ_TOOL_DEFAULT_CONTEXT_BEFORE, READ_TOOL_DEFAULT_CONTEXT_AFTER,
        READ_TOOL_MAX_CONTEXT_BEFORE, READ_TOOL_MAX_CONTEXT_AFTER,
        READ_TOOL_DEFAULT_MAX_MATCHES, READ_TOOL_MAX_MATCHES,
        READ_TOOL_MAX_FILE_SIZE,
        PROJECT_MAX_STORAGE_MB,
        CUSTOM_TOOLS_ENABLED,
        WORKSPACE_SKILLS_DIRNAME,
        PROJECT_AGENTS_SKILLS_DIRNAME,
    )

from modules.file_manager import FileManager
from modules.search_engine import SearchEngine
from modules.terminal_ops import TerminalOperator
from modules.memory_manager import MemoryManager
from modules.terminal_manager import TerminalManager
from modules.todo_manager import TodoManager
from modules.sub_agent import SubAgentManager
from modules.webpage_extractor import extract_webpage_content, tavily_extract
from modules.ocr_client import OCRClient
from modules.easter_egg_manager import EasterEggManager
from modules.personalization_manager import (
    load_personalization_config,
    build_personalization_prompt,
)
from modules.skills_manager import (
    get_skills_catalog,
    build_skills_list,
    merge_enabled_skills,
    build_skills_prompt,
    infer_private_skills_dir,
)
from modules.custom_tool_registry import CustomToolRegistry, build_default_tool_category
from modules.custom_tool_executor import CustomToolExecutor


from core.tool_config import TOOL_CATEGORIES
from utils.api_client import APIClient
from utils.context_manager import ContextManager
from utils.context_manager.fs_access import (
    build_skills_catalog,
    files_for_owner,
    inspect_meta,
    list_dir,
    read_text,
)
from utils.tool_result_formatter import format_tool_result_for_context
from utils.logger import setup_logger
from config.model_profiles import (
    get_model_profile,
    get_model_prompt_replacements,
    get_model_context_window,
)

from modules.i18n import tr

logger = setup_logger(__name__)
DISABLE_LENGTH_CHECK = True

class MainTerminalToolsReadMixin:
    @staticmethod
    def _normalize_skill_name(skill_name: Any) -> str:
                raw = str(skill_name or "").strip()
                if not raw:
                    return ""
                normalized = raw.replace("\\", "/").strip("/")
                for prefix in (f"{WORKSPACE_SKILLS_DIRNAME}/", f"{PROJECT_AGENTS_SKILLS_DIRNAME}/"):
                    if normalized.lower().startswith(prefix):
                        normalized = normalized[len(prefix):]
                        break
                if normalized.lower().endswith("/skill.md"):
                    normalized = normalized[: -len("/SKILL.md")]
                return normalized.strip()

    def _enabled_skills_catalog(self) -> Optional[List[Dict[str, str]]]:
                """read_skill 与 search_skills 共用的可用技能目录；None 表示执行后端读取失败。"""
                try:
                    personalization = load_personalization_config(self.data_dir)
                except Exception:
                    personalization = {}
                scan_project_agents = (
                    bool(personalization.get("agents_skills_scan_enabled", True))
                    if isinstance(personalization, dict)
                    else True
                )
                # 技能目录的“读侧遍历”经执行后端：工作区 .astrion/skills 与
                # .agents/skills 由节点提供；启用过滤仍在控制端（merge_enabled_skills）。
                # 读取失败按失败处理，不回退控制端本机遍历。
                files_access = files_for_owner(self)
                catalog = build_skills_catalog(
                    files_access, scan_project_agents=scan_project_agents
                )
                if catalog is None:
                    return None
                enabled_skills = merge_enabled_skills(
                    personalization.get("enabled_skills") if isinstance(personalization, dict) else None,
                    catalog,
                    personalization.get("skills_catalog_snapshot") if isinstance(personalization, dict) else None,
                )
                enabled_set = set(enabled_skills or [])
                return [item for item in catalog if item.get("id") in enabled_set] if enabled_set else list(catalog)

    def _resolve_skill_id(self, skill_name: Any) -> Dict[str, Any]:
                normalized_input = self._normalize_skill_name(skill_name)
                if not normalized_input:
                    return {"success": False, "error": tr("tools_read.skill_name_empty")}

                filtered_catalog = self._enabled_skills_catalog()
                if filtered_catalog is None:
                    return {"success": False, "error": tr("tools_read.skills_catalog_unavailable")}

                normalized_lower = normalized_input.lower()

                def _resolve_item(item: Dict[str, str]) -> Dict[str, Any]:
                    sid = item.get("id")
                    primary_dir = str(item.get("display_dir") or WORKSPACE_SKILLS_DIRNAME).strip("/")
                    conflict_dir = str(item.get("conflict_dir") or "").strip("/")
                    if conflict_dir:
                        # 同名冲突：报错并引导改用 read_file 按具体路径查看
                        return {
                            "success": False,
                            "error": tr(
                                "tools_read.skill_name_conflict",
                                sid=sid,
                                primary_dir=primary_dir,
                                conflict_dir=conflict_dir,
                            ),
                        }
                    return {"success": True, "skill_id": sid, "display_dir": primary_dir}

                # 1) 优先按 skill id 精确匹配（忽略大小写）
                id_map = {str(item.get("id", "")).lower(): item for item in filtered_catalog if item.get("id")}
                if normalized_lower in id_map:
                    return _resolve_item(id_map[normalized_lower])

                # 2) 再按 label 匹配（忽略大小写）
                label_matches: List[str] = []
                for item in filtered_catalog:
                    label = str(item.get("label") or "").strip().lower()
                    if label and label == normalized_lower and item.get("id"):
                        label_matches.append(item["id"])
                if len(label_matches) == 1:
                    return _resolve_item(id_map[label_matches[0].lower()])
                if len(label_matches) > 1:
                    return {
                        "success": False,
                        "error": tr("tools_read.skill_name_ambiguous", matches=', '.join(sorted(label_matches)))
                    }

                return {"success": False, "error": tr("tools_read.skill_not_found", name=normalized_input)}

    def _handle_read_skill_tool(self, arguments: Dict) -> Dict:
                skill_name = arguments.get("skill_name")
                resolved = self._resolve_skill_id(skill_name)
                if not resolved.get("success"):
                    return resolved

                skill_id = resolved["skill_id"]
                display_dir = str(resolved.get("display_dir") or WORKSPACE_SKILLS_DIRNAME).strip("/")
                from modules.git_worktree.paths import result_path
                read_args = {
                    "path": result_path(self, f'{display_dir}/{skill_id}/SKILL.md'),
                    "type": "read",
                }
                result = self._handle_read_tool(read_args)
                if not result.get("success"):
                    return result

                result["skill_id"] = skill_id
                result["skill_name"] = skill_name
                return result

    SKILL_SEARCH_DESC_MAX_CHARS = 300

    def _handle_search_skills(self, keywords: List[str], max_results: int = 5) -> Dict:
                """处理 search_skills：在可用技能的 SKILL.md（名称/描述/正文）中做关键词检索。

                检索范围与 read_skill 一致（_enabled_skills_catalog）。评分：id/名称命中 +10，
                描述命中 +5，正文每行命中 +1（单关键词最多计 5 行）。描述截断到 300 字，
                片段行号基于完整 SKILL.md，可直接配合 read_file 使用。
                """
                clean_keywords: List[str] = []
                for kw in keywords or []:
                    kw_text = str(kw or "").strip()
                    if kw_text and kw_text not in clean_keywords:
                        clean_keywords.append(kw_text)
                clean_keywords = clean_keywords[:5]
                if not clean_keywords:
                    return {"success": False, "error": tr("tools_read.search_skills_needs_keywords")}
                max_results = self._clamp_int(max_results, 5, 1, 10)

                catalog = self._enabled_skills_catalog()
                if catalog is None:
                    return {"success": False, "error": tr("tools_read.skills_catalog_unavailable")}
                if not catalog:
                    empty_text = tr("tools_read.skills_none_available")
                    return {
                        "success": True, "count": 0, "keywords": clean_keywords,
                        "results": [], "content": empty_text, "summary": empty_text,
                    }

                from modules.git_worktree.paths import result_path
                files_access = files_for_owner(self)
                lowered = [(kw, kw.lower()) for kw in clean_keywords]
                scored: List[Dict[str, Any]] = []
                for item in catalog:
                    skill_id = str(item.get("id") or "")
                    if not skill_id:
                        continue
                    display_dir = str(item.get("display_dir") or WORKSPACE_SKILLS_DIRNAME).strip("/")
                    rel_path = f"{display_dir}/{skill_id}/SKILL.md"
                    text = read_text(files_access, result_path(self, rel_path))
                    if text is None:
                        continue
                    lines = text.split("\n")
                    body_start_idx = 0
                    if lines and lines[0].strip() == "---":
                        for i in range(1, len(lines)):
                            if lines[i].strip() == "---":
                                body_start_idx = i + 1
                                break
                    body_lines = lines[body_start_idx:]
                    name_lower = f"{skill_id}\n{item.get('label') or ''}".lower()
                    description = str(item.get("description") or "").strip()
                    desc_lower = description.lower()

                    score = 0
                    matched_keywords: List[str] = []
                    for kw, kw_lower in lowered:
                        kw_score = 0
                        if kw_lower in name_lower:
                            kw_score += 10
                        if kw_lower in desc_lower:
                            kw_score += 5
                        body_hits = sum(1 for line in body_lines if kw_lower in line.lower())
                        kw_score += min(body_hits, 5)
                        if kw_score > 0:
                            score += kw_score
                            matched_keywords.append(kw)
                    if score <= 0:
                        continue

                    snippets: List[Dict[str, Any]] = []
                    for idx in range(body_start_idx, len(lines)):
                        line_stripped = lines[idx].strip()
                        if not line_stripped:
                            continue
                        line_lower = line_stripped.lower()
                        if any(kw_lower in line_lower for _, kw_lower in lowered):
                            snippet_text = line_stripped if len(line_stripped) <= 120 else line_stripped[:117] + "..."
                            snippets.append({"line": idx + 1, "text": snippet_text})
                        if len(snippets) >= 3:
                            break

                    limit = self.SKILL_SEARCH_DESC_MAX_CHARS
                    if len(description) > limit:
                        description = description[: limit - 3] + "..."
                    conflict_dir = str(item.get("conflict_dir") or "").strip("/")
                    scored.append({
                        "skill_id": skill_id,
                        "path": result_path(self, rel_path),
                        "description": description,
                        "conflict_path": (
                            result_path(self, f"{conflict_dir}/{skill_id}/SKILL.md") if conflict_dir else ""
                        ),
                        "score": score,
                        "matched_keywords": matched_keywords,
                        "snippets": snippets,
                    })

                scored.sort(key=lambda entry: (-entry["score"], -len(entry["matched_keywords"]), entry["skill_id"]))
                top = scored[:max_results]
                keywords_text = ", ".join(clean_keywords)
                if not top:
                    return {
                        "success": True, "count": 0, "keywords": clean_keywords, "results": [],
                        "content": tr("tools_read.skills_search_no_match_content", keywords=keywords_text),
                        "summary": tr("tools_read.skills_search_no_match_summary"),
                    }

                content_lines = [
                    tr("tools_read.skills_search_found_header", count=len(top), keywords=keywords_text),
                    "",
                ]
                for rank, entry in enumerate(top, start=1):
                    content_lines.append(tr(
                        "tools_read.skills_search_result_item", rank=rank, skill_id=entry["skill_id"],
                        path=entry["path"], matched=", ".join(entry["matched_keywords"]),
                    ))
                    if entry["conflict_path"]:
                        content_lines.append(tr(
                            "tools_read.skills_search_result_conflict", conflict_path=entry["conflict_path"],
                        ))
                    if entry["description"]:
                        content_lines.append(tr("tools_read.search_result_desc", description=entry["description"]))
                    if entry["snippets"]:
                        content_lines.append(tr("tools_read.search_result_snippets_label"))
                        for snippet in entry["snippets"]:
                            content_lines.append(f"      L{snippet['line']}: {snippet['text']}")
                    content_lines.append("")
                content_lines.append(tr("tools_read.skills_search_read_full_hint"))
                return {
                    "success": True,
                    "count": len(top),
                    "keywords": clean_keywords,
                    "results": top,
                    "content": "\n".join(content_lines).strip(),
                    "summary": tr("tools_read.skills_search_found_summary", count=len(top)),
                }

    def _handle_recall_project_memory(self, name: str) -> Dict:
                """Read a topic by its workspace-local name."""
                from modules.project_memory import memory_filename, tool_directory

                safe_name = str(name).strip()
                filename = f"{safe_name}.md"
                if not memory_filename(filename) or not safe_name or "/" in safe_name or "\\" in safe_name:
                    return {"success": False, "error": tr("tools_read.memory_name_invalid", name=name)}
                file_path = f"{tool_directory(self)}/{filename}"
                read_args = {
                    "path": file_path,
                    "type": "read",
                }
                result = self._handle_read_tool(read_args)
                if result.get("success"):
                    result["memory_name"] = safe_name
                return result

    def _handle_search_project_memory(self, keywords: List[str], max_results: int = 5) -> Dict:
                """在当前工作区的专题记忆中做关键词全文检索。

                评分：名称命中 +10，描述命中 +5，正文每行命中 +1（单关键词最多计 5 行）。
                返回 top-N 结果，附匹配行片段（行号基于完整文件，可直接配合 read_file extract 使用）。
                """
                clean_keywords: List[str] = []
                for kw in keywords or []:
                    kw_text = str(kw or "").strip()
                    if kw_text and kw_text not in clean_keywords:
                        clean_keywords.append(kw_text)
                clean_keywords = clean_keywords[:5]
                if not clean_keywords:
                    return {"success": False, "error": tr("tools_read.search_memory_needs_keywords")}

                max_results = self._clamp_int(max_results, 5, 1, 10)
                # 项目记忆目录的列举与内容读取均经执行后端（不再对本机 Path 做
                # resolve/exists/glob）。
                files_access = files_for_owner(self)
                from modules.project_memory import memory_filename, tool_directory
                memory_directory = tool_directory(self)
                memory_meta = inspect_meta(files_access, memory_directory)
                if not memory_meta.get("success"):
                    return {
                        "success": False,
                        "error": memory_meta.get("error") or "无法读取项目记忆目录",
                    }
                if not memory_meta.get("exists") or not memory_meta.get("is_dir"):
                    empty_text = tr("tools_read.memory_dir_not_exists")
                    return {
                        "success": True,
                        "count": 0,
                        "keywords": clean_keywords,
                        "results": [],
                        "content": empty_text,
                        "summary": empty_text,
                    }

                listing = list_dir(files_access, memory_directory)
                if not listing.get("success"):
                    return {
                        "success": False,
                        "error": listing.get("error") or "无法列出项目记忆目录",
                    }
                memory_files = [
                    item for item in (listing.get("files") or [])
                    if memory_filename(str(item.get("name") or ""))
                    and item.get("type") in (None, "file")
                ]

                lowered = [(kw, kw.lower()) for kw in clean_keywords]
                scored: List[Dict[str, Any]] = []
                for memory_file in sorted(
                    memory_files, key=lambda item: str(item.get("name") or "")
                ):
                    file_name = str(memory_file.get("name") or "")
                    file_path = f"{memory_directory}/{file_name}"
                    text = read_text(files_access, file_path)
                    if text is None:
                        continue

                    lines = text.split("\n")
                    memory_name = file_name[:-3]
                    name = memory_name
                    description = ""
                    body_start_idx = 0  # 0-based，正文起始行（跳过 frontmatter）
                    if lines and lines[0].strip() == "---":
                        for i in range(1, len(lines)):
                            if lines[i].strip() == "---":
                                for fm_line in lines[1:i]:
                                    fm_stripped = fm_line.strip()
                                    if fm_stripped.startswith("name:"):
                                        name = fm_stripped.split(":", 1)[1].strip() or name
                                    elif fm_stripped.startswith("description:"):
                                        description = fm_stripped.split(":", 1)[1].strip()
                                body_start_idx = i + 1
                                break

                    searchable_names = (name.lower(), memory_name.lower())
                    desc_lower = description.lower()
                    body_lines = lines[body_start_idx:]

                    score = 0
                    matched_keywords: List[str] = []
                    for kw, kw_lower in lowered:
                        kw_score = 0
                        if any(kw_lower in value for value in searchable_names):
                            kw_score += 10
                        if kw_lower in desc_lower:
                            kw_score += 5
                        body_hits = sum(1 for line in body_lines if kw_lower in line.lower())
                        kw_score += min(body_hits, 5)
                        if kw_score > 0:
                            score += kw_score
                            matched_keywords.append(kw)

                    if score <= 0:
                        continue

                    snippets: List[Dict[str, Any]] = []
                    for idx in range(body_start_idx, len(lines)):
                        line_stripped = lines[idx].strip()
                        if not line_stripped:
                            continue
                        line_lower = line_stripped.lower()
                        if any(kw_lower in line_lower for _, kw_lower in lowered):
                            snippet_text = line_stripped if len(line_stripped) <= 120 else line_stripped[:117] + "..."
                            snippets.append({"line": idx + 1, "text": snippet_text})
                        if len(snippets) >= 3:
                            break

                    scored.append({
                        "file": file_name,
                        "name": name,
                        "memory_name": memory_name,
                        "description": description,
                        "score": score,
                        "matched_keywords": matched_keywords,
                        "snippets": snippets,
                    })

                scored.sort(key=lambda item: (-item["score"], -len(item["matched_keywords"]), item["name"]))
                top = scored[:max_results]

                if not top:
                    empty_text = tr(
                        "tools_read.search_no_match_content",
                        keywords="、".join(clean_keywords),
                    )
                    return {
                        "success": True,
                        "count": 0,
                        "keywords": clean_keywords,
                        "results": [],
                        "content": empty_text,
                        "summary": tr("tools_read.search_no_match_summary"),
                    }

                content_lines = [
                    tr(
                        "tools_read.search_found_header",
                        count=len(top),
                        keywords="、".join(clean_keywords),
                    ),
                    "",
                ]
                for rank, item in enumerate(top, start=1):
                    content_lines.append(tr("tools_read.search_result_item", rank=rank, name=item['name'], file=item['file']))
                    if item["description"]:
                        content_lines.append(tr("tools_read.search_result_desc", description=item['description']))
                    if item["snippets"]:
                        content_lines.append(tr("tools_read.search_result_snippets_label"))
                        for snippet in item["snippets"]:
                            content_lines.append(f"      L{snippet['line']}: {snippet['text']}")
                    content_lines.append("")
                content_lines.append(tr("tools_read.search_read_full_hint"))
                content_text = "\n".join(content_lines).strip()

                return {
                    "success": True,
                    "count": len(top),
                    "keywords": clean_keywords,
                    "results": top,
                    "content": content_text,
                    "summary": tr("tools_read.search_found_summary", count=len(top)),
                }

    @staticmethod
    def _clamp_int(value, default, min_value=None, max_value=None):
                """将输入转换为整数并限制范围。"""
                if value is None:
                    return default
                try:
                    num = int(value)
                except (TypeError, ValueError):
                    return default
                if min_value is not None:
                    num = max(min_value, num)
                if max_value is not None:
                    num = min(max_value, num)
                return num

    @staticmethod
    def _parse_optional_line(value, field_name: str):
                """解析可选的行号参数。"""
                if value is None:
                    return None, None
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    return None, tr("tools_read.param_must_be_int", field_name=field_name)
                if number < 1:
                    return None, tr("tools_read.param_must_be_gte_1", field_name=field_name)
                return number, None

    @staticmethod
    def _truncate_text_block(text: str, max_chars: int):
                """对单段文本应用字符限制。"""
                if max_chars and len(text) > max_chars:
                    return text[:max_chars], True, max_chars
                return text, False, len(text)

    @staticmethod
    def _limit_text_chunks(chunks: List[Dict], text_key: str, max_chars: int):
                """对多个文本片段应用全局字符限制。"""
                if max_chars is None or max_chars <= 0:
                    return chunks, False, sum(len(chunk.get(text_key, "") or "") for chunk in chunks)

                remaining = max_chars
                limited_chunks: List[Dict] = []
                truncated = False
                consumed = 0

                for chunk in chunks:
                    snippet = chunk.get(text_key, "") or ""
                    snippet_len = len(snippet)
                    chunk_copy = dict(chunk)

                    if remaining <= 0:
                        truncated = True
                        break

                    if snippet_len > remaining:
                        chunk_copy[text_key] = snippet[:remaining]
                        chunk_copy["truncated"] = True
                        consumed += remaining
                        limited_chunks.append(chunk_copy)
                        truncated = True
                        remaining = 0
                        break

                    limited_chunks.append(chunk_copy)
                    consumed += snippet_len
                    remaining -= snippet_len

                return limited_chunks, truncated, consumed

    def _handle_read_tool(self, arguments: Dict) -> Dict:
                """集中处理 read_file 工具的三种模式。"""
                file_path = arguments.get("path")
                if not file_path:
                    return {"success": False, "error": tr("tools_read.missing_file_path")}

                read_type = (arguments.get("type") or "read").lower()
                if read_type not in {"read", "search", "extract"}:
                    return {"success": False, "error": tr("tools_read.unknown_read_type", read_type=read_type)}

                max_chars = self._clamp_int(
                    arguments.get("max_chars"),
                    READ_TOOL_DEFAULT_MAX_CHARS,
                    1,
                    MAX_READ_FILE_CHARS
                )

                base_result = {
                    "success": True,
                    "type": read_type,
                    "path": None,
                    "encoding": "utf-8",
                    "max_chars": max_chars,
                    "truncated": False
                }

                if read_type == "read":
                    start_line, error = self._parse_optional_line(arguments.get("start_line"), "start_line")
                    if error:
                        return {"success": False, "error": error}
                    end_line_val = arguments.get("end_line")
                    end_line = None
                    if end_line_val is not None:
                        end_line, error = self._parse_optional_line(end_line_val, "end_line")
                        if error:
                            return {"success": False, "error": error}
                        if start_line and end_line < start_line:
                            return {"success": False, "error": tr("tools_read.end_line_ge_start_line")}

                    files_access = files_for_owner(self)
                    read_result = files_access.read_text_segment(
                        file_path,
                        start_line=start_line,
                        end_line=end_line,
                        size_limit=READ_TOOL_MAX_FILE_SIZE
                    )
                    if not read_result.get("success"):
                        return read_result

                    from modules.git_worktree.paths import result_path
                    read_result['path'] = result_path(self, read_result['path'])
                    content, truncated, char_count = self._truncate_text_block(read_result["content"], max_chars)
                    base_result.update({
                        "path": read_result["path"],
                        "content": content,
                        "line_start": read_result["line_start"],
                        "line_end": read_result["line_end"],
                        "total_lines": read_result["total_lines"],
                        "file_size": read_result["size"],
                        "char_count": char_count,
                        "message": tr(
                            "tools_read.read_success",
                            path=read_result["path"],
                            line_start=read_result["line_start"],
                            line_end=read_result["line_end"],
                        )
                    })
                    base_result["truncated"] = truncated
                    # 整文件读取已持有原文，直接交给上下文缓存，避免隐式再读一次；
                    # 分段读取才让 load_file 经同一执行后端补读整文件。
                    if start_line is None and end_line is None:
                        self.context_manager.load_file(
                            read_result["path"], content=read_result["content"]
                        )
                    else:
                        self.context_manager.load_file(read_result["path"])
                    return base_result

                if read_type == "search":
                    query = arguments.get("query")
                    if not query:
                        return {"success": False, "error": tr("tools_read.search_requires_query")}

                    max_matches = self._clamp_int(
                        arguments.get("max_matches"),
                        READ_TOOL_DEFAULT_MAX_MATCHES,
                        1,
                        READ_TOOL_MAX_MATCHES
                    )
                    context_before = self._clamp_int(
                        arguments.get("context_before"),
                        READ_TOOL_DEFAULT_CONTEXT_BEFORE,
                        0,
                        READ_TOOL_MAX_CONTEXT_BEFORE
                    )
                    context_after = self._clamp_int(
                        arguments.get("context_after"),
                        READ_TOOL_DEFAULT_CONTEXT_AFTER,
                        0,
                        READ_TOOL_MAX_CONTEXT_AFTER
                    )
                    case_sensitive = bool(arguments.get("case_sensitive"))

                    files_access = files_for_owner(self)
                    search_result = files_access.search_text(
                        file_path,
                        query=query,
                        max_matches=max_matches,
                        context_before=context_before,
                        context_after=context_after,
                        case_sensitive=case_sensitive,
                        size_limit=READ_TOOL_MAX_FILE_SIZE
                    )
                    if not search_result.get("success"):
                        return search_result

                    from modules.git_worktree.paths import result_path
                    search_result['path'] = result_path(self, search_result['path'])
                    matches = search_result["matches"]
                    limited_matches, truncated, char_count = self._limit_text_chunks(matches, "snippet", max_chars)

                    base_result.update({
                        "path": search_result["path"],
                        "file_size": search_result["size"],
                        "query": query,
                        "max_matches": max_matches,
                        "actual_matches": len(matches),
                        "returned_matches": len(limited_matches),
                        "context_before": context_before,
                        "context_after": context_after,
                        "case_sensitive": case_sensitive,
                        "matches": limited_matches,
                        "char_count": char_count,
                        "message": tr(
                            "tools_read.search_success",
                            path=search_result["path"],
                            query=query,
                            count=len(limited_matches),
                        )
                    })
                    base_result["truncated"] = truncated
                    return base_result

                # extract
                segments = arguments.get("segments")
                if not isinstance(segments, list) or not segments:
                    return {"success": False, "error": tr("tools_read.extract_requires_segments")}

                files_access = files_for_owner(self)
                extract_result = files_access.extract_segments(
                    file_path,
                    segments=segments,
                    size_limit=READ_TOOL_MAX_FILE_SIZE
                )
                if not extract_result.get("success"):
                    return extract_result
                from modules.git_worktree.paths import result_path
                extract_result['path'] = result_path(self, extract_result['path'])

                limited_segments, truncated, char_count = self._limit_text_chunks(
                    extract_result["segments"],
                    "content",
                    max_chars
                )

                base_result.update({
                    "path": extract_result["path"],
                    "segments": limited_segments,
                    "file_size": extract_result["size"],
                    "total_lines": extract_result["total_lines"],
                    "segment_count": len(limited_segments),
                    "char_count": char_count,
                    "message": tr(
                        "tools_read.extract_success",
                        path=extract_result["path"],
                        count=len(limited_segments),
                    )
                })
                base_result["truncated"] = truncated
                self.context_manager.load_file(extract_result["path"])
                return base_result
