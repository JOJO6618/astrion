"""文件相关共享路由（主 SPA 使用）：项目结构、目录列举、下载、文本读写、@文件搜索。

原 /file-manager 独立页面的写操作端点（create/delete/rename/copy/move/upload/batch）
已随该页面下线移除；页面路由见 git 历史。
"""
from __future__ import annotations
import os
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from flask import Blueprint, jsonify, request, send_file

from .auth_helpers import api_login_required, resolve_admin_policy, get_current_user_record
from .context import with_terminal, get_gui_manager
from .utils_common import debug_log

files_bp = Blueprint("files", __name__)

@files_bp.route('/api/files')
@api_login_required
@with_terminal
def get_files(terminal, workspace, username):
    policy = resolve_admin_policy(get_current_user_record())
    if policy.get("ui_blocks", {}).get("collapse_workspace") or policy.get("ui_blocks", {}).get("block_file_manager"):
        return jsonify({"success": False, "error": "文件浏览已被管理员禁用"}), 403
    structure = terminal.context_manager.get_project_structure()
    return jsonify(structure)


def _format_entry(entry) -> Dict[str, Any]:
    return {
        "name": entry.name,
        "path": entry.path,
        "type": entry.type,
        "size": entry.size,
        "modified_at": entry.modified_at,
        "extension": entry.extension,
        "is_editable": entry.is_editable,
    }


@files_bp.route('/api/gui/files/entries', methods=['GET'])
@api_login_required
@with_terminal
def gui_list_entries(terminal, workspace, username):
    policy = resolve_admin_policy(get_current_user_record())
    if policy.get("ui_blocks", {}).get("block_file_manager"):
        return jsonify({"success": False, "error": "文件管理已被管理员禁用"}), 403
    relative_path = request.args.get('path') or ""
    manager = get_gui_manager(workspace)
    try:
        resolved_path, entries = manager.list_directory(relative_path)
        breadcrumb = manager.breadcrumb(resolved_path)
        return jsonify({
            "success": True,
            "data": {
                "path": resolved_path,
                "breadcrumb": breadcrumb,
                "items": [_format_entry(entry) for entry in entries]
            }
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@files_bp.route('/api/gui/files/download', methods=['GET'])
@api_login_required
@with_terminal
def gui_download_entry(terminal, workspace, username):
    path = request.args.get('path')
    if not path:
        return jsonify({"success": False, "error": "缺少 path"}), 400
    manager = get_gui_manager(workspace)
    try:
        target = manager.prepare_download(path)
        if target.is_dir():
            memory_file = BytesIO()
            with zipfile.ZipFile(memory_file, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(target):
                    for file in files:
                        full_path = Path(root) / file
                        arcname = manager._to_relative(full_path)
                        zf.write(full_path, arcname=arcname)
            memory_file.seek(0)
            download_name = f"{target.name}.zip"
            return send_file(memory_file, as_attachment=True, download_name=download_name, mimetype='application/zip')
        return send_file(target, as_attachment=True, download_name=target.name)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@files_bp.route('/api/gui/files/text', methods=['GET', 'POST'])
@api_login_required
@with_terminal
def gui_text_entry(terminal, workspace, username):
    manager = get_gui_manager(workspace)
    if request.method == 'GET':
        path = request.args.get('path')
        if not path:
            return jsonify({"success": False, "error": "缺少 path"}), 400
        try:
            content, modified = manager.read_text(path)
            return jsonify({"success": True, "path": path, "content": content, "modified_at": modified})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

    payload = request.get_json() or {}
    path = payload.get('path')
    content = payload.get('content')
    if path is None or content is None:
        return jsonify({"success": False, "error": "缺少 path 或 content"}), 400
    try:
        result = manager.write_text(path, content)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


def _score_file_match(path: str, query: str) -> Optional[Tuple[int, ...]]:
    """为 @文件 搜索计算匹配分数；分数越低越靠前，None 表示不匹配。"""
    path_lower = path.lower()
    query_lower = query.lower()

    if path_lower.startswith(query_lower):
        return (0, len(path))

    query_parts = query_lower.split('/')
    path_parts = path_lower.split('/')

    if len(query_parts) > 1 and len(path_parts) >= len(query_parts):
        matched = True
        for i, q in enumerate(query_parts):
            p = path_parts[i]
            if not p.startswith(q) and q not in p:
                matched = False
                break
        if matched:
            return (1, len(path))

    name = path_parts[-1] if path_parts else path_lower
    if name.startswith(query_lower):
        return (2, len(path))

    if query_lower in name:
        return (3, len(path))

    if query_lower in path_lower:
        return (4, len(path))

    return None


def _scan_project_entries(project_path: Path, max_depth: int = 6) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """扫描项目目录，返回文件和文件夹列表（包含隐藏目录）。"""
    files: List[Dict[str, Any]] = []
    folders: List[Dict[str, Any]] = []

    for root, dirs, filenames in os.walk(project_path):
        rel_root = Path(root).relative_to(project_path)
        level = len(rel_root.parts) if str(rel_root) != '.' else 0
        if level > max_depth:
            del dirs[:]
            continue

        for d in list(dirs):
            dir_path = rel_root / d if str(rel_root) != '.' else Path(d)
            folders.append({
                "name": d,
                "path": str(dir_path).replace('\\', '/'),
                "type": "directory"
            })

        for f in filenames:
            file_path = rel_root / f if str(rel_root) != '.' else Path(f)
            ext = Path(f).suffix.lower()
            files.append({
                "name": f,
                "path": str(file_path).replace('\\', '/'),
                "type": "file",
                "extension": ext
            })

    return files, folders


def _scan_root_entries(project_path: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """只扫描项目根目录的直接子项（包含隐藏目录）。"""
    files: List[Dict[str, Any]] = []
    folders: List[Dict[str, Any]] = []
    if not project_path.exists() or not project_path.is_dir():
        return files, folders
    for entry in sorted(project_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if entry.is_dir():
            folders.append({
                "name": entry.name,
                "path": entry.name,
                "type": "directory"
            })
        elif entry.is_file():
            files.append({
                "name": entry.name,
                "path": entry.name,
                "type": "file",
                "extension": entry.suffix.lower()
            })
    return files, folders


@files_bp.route('/api/project/files/search', methods=['GET'])
@api_login_required
@with_terminal
def search_project_files(terminal, workspace, username):
    """为 @文件 功能提供项目内文件/文件夹搜索（包含隐藏目录）。"""
    policy = resolve_admin_policy(get_current_user_record())
    if policy.get("ui_blocks", {}).get("collapse_workspace") or policy.get("ui_blocks", {}).get("block_file_manager"):
        return jsonify({"success": False, "error": "文件浏览已被管理员禁用"}), 403

    query = str(request.args.get('q') or '').strip()

    try:
        project_path = Path(getattr(workspace, 'project_path', '') or '').expanduser().resolve()
        if not project_path.exists():
            return jsonify({"success": False, "error": "项目路径不存在"}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": f"项目路径无效: {exc}"}), 400

    try:
        files, folders = _scan_project_entries(project_path, max_depth=6)
    except Exception as exc:
        return jsonify({"success": False, "error": f"扫描项目失败: {exc}"}), 500

    if not query:
        root_files, root_folders = _scan_root_entries(project_path)
        root_items = root_folders + root_files
        return jsonify({
            "success": True,
            "data": {
                "items": root_items[:50],
                "total": len(root_items)
            }
        })

    scored: List[Tuple[Tuple[int, ...], Dict[str, Any]]] = []
    for entry in files + folders:
        score = _score_file_match(entry["path"], query)
        if score is not None:
            scored.append((score, entry))

    scored.sort(key=lambda x: (x[0], x[1]["path"].lower()))
    limit = max(10, min(100, int(request.args.get('limit') or 50)))
    return jsonify({
        "success": True,
        "data": {
            "items": [entry for _, entry in scored[:limit]],
            "total": len(scored)
        }
    })


__all__ = ["files_bp"]
