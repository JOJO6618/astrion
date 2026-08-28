"""Workspace skill 读取与 /api/skills 接口。"""
from __future__ import annotations
from server.tasks import tasks_bp
import re
from pathlib import Path
from typing import Dict, Any, List

from flask import request, jsonify
from flask import session

from server.auth_helpers import api_login_required, get_current_username
from server.context import get_user_resources
from server.utils_common import debug_log
from config import WORKSPACE_SKILLS_DIRNAME
from modules.skills_manager import wait_skill_file_ready
from modules.i18n import tr


SKILL_FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n?", re.S)
SKILL_FIELD_RE = re.compile(r"^(?P<key>name|description)\s*:\s*(?P<value>.*)$")



def _parse_skill_metadata(content: str, fallback_name: str) -> Dict[str, str]:
    metadata = {"name": fallback_name, "description": ""}
    match = SKILL_FRONTMATTER_RE.match(content or "")
    if not match:
        return metadata
    for raw_line in match.group("body").splitlines():
        field = SKILL_FIELD_RE.match(raw_line.strip())
        if not field:
            continue
        key = field.group("key")
        value = field.group("value").strip().strip('"').strip("'")
        metadata[key] = value
    metadata["name"] = metadata.get("name") or fallback_name
    return metadata

def _workspace_project_root(workspace) -> Path:
    """工作区根目录（已 resolve 的绝对路径）。"""
    return Path(workspace.project_path).expanduser().resolve()

def _workspace_skills_dir(workspace) -> Path:
    return _workspace_project_root(workspace) / WORKSPACE_SKILLS_DIRNAME

def _rel_skill_path(skill_file: Path, workspace) -> str:
    """把 skill 文件的绝对路径转成相对工作区根的路径（如 `.astrion/skills/xxx/SKILL.md`）。

    前端 / 菜单插入、对话历史、任务快照均使用该相对路径，避免暴露服务器绝对路径。
    """
    return str(skill_file.relative_to(_workspace_project_root(workspace)).as_posix())

def _resolve_workspace_skill_path(workspace, raw_path: str) -> Path:
    try:
        skills_dir = _workspace_skills_dir(workspace)
    except Exception as exc:
        debug_log(f"[SkillsAPI] resolve skill cannot compute skills_dir: {exc}")
        raise ValueError(tr("tasks.skill_dir_unavailable", error=exc)) from exc
    debug_log(f"[SkillsAPI] resolve skill skills_dir={skills_dir!r} raw_path={raw_path!r}")
    try:
        target = Path(str(raw_path or "")).expanduser()
        if not target.is_absolute():
            target = Path(workspace.project_path).expanduser().resolve() / target
        target = target.resolve()
    except Exception as exc:
        debug_log(f"[SkillsAPI] resolve skill cannot resolve path: {exc}")
        raise ValueError(tr("tasks.skill_path_resolve_failed", error=exc)) from exc
    debug_log(f"[SkillsAPI] resolve skill resolved_target={target!r} name={target.name!r}")
    if target.name != "SKILL.md":
        raise ValueError(tr("tasks.skill_path_not_skillmd", name=target.name))
    try:
        target.relative_to(skills_dir)
    except ValueError as exc:
        debug_log(f"[SkillsAPI] resolve skill path outside skills_dir: target={target!r} skills_dir={skills_dir!r}")
        raise ValueError(tr("tasks.skill_path_outside")) from exc
    # 工作区 skills 目录可能被并发的全量同步（rmtree+重建）短暂清空，
    # 直接 is_file() 一次失败会把瞬时窗口误判为「skill 文件不存在」拒绝任务，
    # 这里改为有界等待重试，等过同步窗口再下结论。
    if not wait_skill_file_ready(target, skills_dir):
        debug_log(f"[SkillsAPI] resolve skill file not found (after wait): {target!r}")
        raise ValueError(tr("tasks.skill_not_found"))
    return target

def _list_workspace_skills(workspace) -> List[Dict[str, str]]:
    skills_dir = _workspace_skills_dir(workspace)
    if not skills_dir.is_dir():
        return []
    result: List[Dict[str, str]] = []
    for skill_file in sorted(skills_dir.glob("*/SKILL.md"), key=lambda p: p.parent.name.lower()):
        try:
            content = skill_file.read_text(encoding="utf-8")
        except Exception:
            continue
        metadata = _parse_skill_metadata(content, skill_file.parent.name)
        result.append({
            "name": metadata.get("name") or skill_file.parent.name,
            "description": metadata.get("description") or "",
            "path": _rel_skill_path(skill_file, workspace),
        })
    return result

def _build_skill_context_messages(workspace, raw_refs: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_refs, list):
        debug_log(f"[SkillsAPI] build_skill_context raw_refs is not list: {type(raw_refs).__name__}")
        return []
    messages: List[Dict[str, str]] = []
    seen_paths = set()
    for idx, item in enumerate(raw_refs[:10]):
        if not isinstance(item, dict):
            debug_log(f"[SkillsAPI] build_skill_context item[{idx}] not dict: {item!r}")
            continue
        raw_path = str(item.get("path") or "").strip()
        debug_log(f"[SkillsAPI] build_skill_context item[{idx}] raw_path={raw_path!r} name={item.get('name')!r}")
        if not raw_path:
            continue
        skill_file = _resolve_workspace_skill_path(workspace, raw_path)
        path_key = _rel_skill_path(skill_file, workspace)
        if path_key in seen_paths:
            debug_log(f"[SkillsAPI] build_skill_context duplicate path skipped: {path_key}")
            continue
        seen_paths.add(path_key)
        try:
            content = skill_file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(tr("tasks.skill_encoding_error", name=skill_file.name)) from exc
        except OSError as exc:
            raise ValueError(tr("tasks.skill_read_failed", error=exc)) from exc
        metadata = _parse_skill_metadata(content, skill_file.parent.name)
        messages.append({
            "name": metadata.get("name") or skill_file.parent.name,
            "path": path_key,
            "content": content,
        })
    return messages

@tasks_bp.route("/api/skills", methods=["GET"])
@api_login_required
def list_skills_api():
    username = get_current_username()
    workspace_id = session.get("workspace_id") or "default"
    try:
        _terminal, workspace = get_user_resources(username, workspace_id)
        if not workspace:
            return jsonify({"success": False, "error": tr("tasks.workspace_unavailable")}), 400
        return jsonify({
            "success": True,
            "skills": _list_workspace_skills(workspace),
        })
    except Exception as exc:
        debug_log(f"[SkillsAPI] 列出技能失败: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500
