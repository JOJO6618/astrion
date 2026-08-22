from __future__ import annotations
from server.chat import chat_bp
import json, time
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
from io import BytesIO
import zipfile
import os

from flask import Blueprint, jsonify, request, session, send_file
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import secrets
import mimetypes

from config import MAX_UPLOAD_SIZE, OUTPUT_FORMATS
from modules.personalization_manager import (
    load_personalization_config,
    resolve_context_compression_settings,
    save_personalization_config,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX,
)
from modules.skills_manager import (
    get_skills_catalog,
    infer_private_skills_dir,
    merge_enabled_skills,
    sync_workspace_skills,
)
from modules.upload_security import UploadSecurityError
from modules.host_sandbox_policy import load_policy, save_policy
from modules.user_manager import UserWorkspace
from core.web_terminal import WebTerminal
from config.model_profiles import get_model_context_window

from server.auth_helpers import api_login_required, resolve_admin_policy, get_current_user_record, get_current_username
from server.context import with_terminal, get_gui_manager, get_upload_guard, build_upload_error_response, ensure_conversation_loaded, get_or_create_usage_tracker
from server.security import rate_limited, prune_socket_tokens
from server.utils_common import debug_log
from server.state import PROJECT_MAX_STORAGE_MB, pending_socket_tokens, SOCKET_TOKEN_TTL_SECONDS
from server.state import tool_approval_manager, user_question_manager
from server.extensions import socketio
from server.monitor import get_cached_monitor_snapshot
from server.utils_common import sanitize_filename_preserve_unicode

UPLOAD_FOLDER_NAME = ".astrion/user_upload"


def _derive_filename_from_upload(uploaded_file) -> str:
    """当上传请求未提供文件名时，根据 MIME 类型或文件扩展名推断一个兜底文件名。"""
    mime_type = (uploaded_file.content_type or '').strip().lower()
    if mime_type:
        # 优先使用 mimetypes 库的 guess_extension，它通常比硬编码映射更全面
        ext = mimetypes.guess_extension(mime_type, strict=False)
        if ext:
            return f"upload_{int(time.time())}{ext}"
    # 常见类型的兜底映射
    type_map = {
        'image/jpeg': '.jpg',
        'image/jpg': '.jpg',
        'image/png': '.png',
        'image/gif': '.gif',
        'image/webp': '.webp',
        'image/bmp': '.bmp',
        'image/svg+xml': '.svg',
        'video/mp4': '.mp4',
        'video/quicktime': '.mov',
        'video/x-matroska': '.mkv',
        'video/avi': '.avi',
        'video/webm': '.webm',
        'application/pdf': '.pdf',
        'text/plain': '.txt',
        'text/markdown': '.md',
    }
    ext = type_map.get(mime_type)
    if ext:
        return f"upload_{int(time.time())}{ext}"
    return ''


@chat_bp.route('/api/upload', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("legacy_upload", 20, 300, scope="user")
def upload_file(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """处理前端文件上传请求"""
    policy = resolve_admin_policy(get_current_user_record())
    if policy.get("ui_blocks", {}).get("block_upload"):
        return jsonify({
            "success": False,
            "error": "文件上传已被管理员禁用",
            "message": "被管理员禁用上传"
        }), 403
    if 'file' not in request.files:
        return jsonify({
            "success": False,
            "error": "未找到文件",
            "message": "请求中缺少文件字段"
        }), 400

    uploaded_file = request.files['file']
    original_name = (request.form.get('filename') or '').strip()

    if not uploaded_file:
        return jsonify({
            "success": False,
            "error": "未找到文件",
            "message": "请求中缺少文件内容"
        }), 400

    raw_name = original_name or uploaded_file.filename or ''
    if not raw_name.strip():
        # 文件名缺失时，尝试根据 MIME 类型或文件内容推断一个兜底文件名
        raw_name = _derive_filename_from_upload(uploaded_file)

    if not raw_name.strip():
        return jsonify({
            "success": False,
            "error": "文件名为空",
            "message": "请选择要上传的文件"
        }), 400

    filename = sanitize_filename_preserve_unicode(raw_name)
    if not filename:
        filename = secure_filename(raw_name)
    if not filename:
        return jsonify({
            "success": False,
            "error": "非法文件名",
            "message": "文件名包含不支持的字符"
        }), 400

    file_manager = getattr(terminal, 'file_manager', None)
    if file_manager is None:
        return jsonify({
            "success": False,
            "error": "文件管理器未初始化"
        }), 500

    target_folder_relative = UPLOAD_FOLDER_NAME
    valid_folder, folder_error, folder_path = file_manager._validate_path(target_folder_relative)
    if not valid_folder:
        return jsonify({
            "success": False,
            "error": folder_error
        }), 400

    try:
        folder_path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": f"创建上传目录失败: {exc}"
        }), 500

    target_relative = str(Path(target_folder_relative) / filename)
    valid_file, file_error, target_full_path = file_manager._validate_path(target_relative)
    if not valid_file:
        return jsonify({
            "success": False,
            "error": file_error
        }), 400

    final_path = target_full_path
    if final_path.exists():
        stem = final_path.stem
        suffix = final_path.suffix
        counter = 1

        while final_path.exists():
            candidate_name = f"{stem}_{counter}{suffix}"
            target_relative = str(Path(target_folder_relative) / candidate_name)
            valid_file, file_error, candidate_path = file_manager._validate_path(target_relative)
            if not valid_file:
                return jsonify({
                    "success": False,
                    "error": file_error
                }), 400
            final_path = candidate_path
            counter += 1

    try:
        relative_path = str(final_path.relative_to(workspace.project_path))
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": f"路径解析失败: {exc}"
        }), 400

    guard = get_upload_guard(workspace)
    try:
        result = guard.process_upload(
            uploaded_file,
            final_path,
            username=username,
            source="legacy_upload",
            original_name=raw_name,
            relative_path=relative_path,
        )
    except UploadSecurityError as exc:
        return build_upload_error_response(exc)
    except Exception as exc:
        return jsonify({
            "success": False,
            "error": f"保存文件失败: {exc}"
        }), 500

    metadata = result.get("metadata", {})
    print(f"{OUTPUT_FORMATS['file']} 上传文件: {relative_path}")

    return jsonify({
        "success": True,
        "path": relative_path,
        "filename": final_path.name,
        "folder": target_folder_relative,
        "scan": metadata.get("scan"),
        "sha256": metadata.get("sha256"),
        "size": metadata.get("size"),
    })

@chat_bp.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(error):
    """全局捕获上传超大小"""
    size_mb = MAX_UPLOAD_SIZE / (1024 * 1024)
    return jsonify({
        "success": False,
        "error": "文件过大",
        "message": f"单个文件大小不可超过 {size_mb:.1f} MB"
    }), 413

@chat_bp.route('/api/download/file')
@api_login_required
@with_terminal
def download_file_api(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """下载单个文件"""
    path = (request.args.get('path') or '').strip()
    if not path:
        return jsonify({"success": False, "error": "缺少路径参数"}), 400

    valid, error, full_path = terminal.file_manager._validate_path(path)
    if not valid or full_path is None:
        return jsonify({"success": False, "error": error or "路径校验失败"}), 400
    if not full_path.exists() or not full_path.is_file():
        return jsonify({"success": False, "error": "文件不存在"}), 404

    return send_file(
        full_path,
        as_attachment=True,
        download_name=full_path.name
    )

@chat_bp.route('/api/download/folder')
@api_login_required
@with_terminal
def download_folder_api(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """打包并下载文件夹"""
    path = (request.args.get('path') or '').strip()
    if not path:
        return jsonify({"success": False, "error": "缺少路径参数"}), 400

    valid, error, full_path = terminal.file_manager._validate_path(path)
    if not valid or full_path is None:
        return jsonify({"success": False, "error": error or "路径校验失败"}), 400
    if not full_path.exists() or not full_path.is_dir():
        return jsonify({"success": False, "error": "文件夹不存在"}), 404

    buffer = BytesIO()
    folder_name = Path(path).name or full_path.name or "archive"

    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_buffer:
        # 确保目录本身被包含
        zip_buffer.write(full_path, arcname=folder_name + '/')

        for item in full_path.rglob('*'):
            relative_name = Path(folder_name) / item.relative_to(full_path)
            if item.is_dir():
                zip_buffer.write(item, arcname=str(relative_name) + '/')
            else:
                zip_buffer.write(item, arcname=str(relative_name))

    buffer.seek(0)
    return send_file(
        buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f"{folder_name}.zip"
    )


# 文件预览允许 inline 展示的 MIME 前缀白名单
# 任何不在此白名单内的类型一律退化为 application/octet-stream（浏览器会触发下载）
_FILE_CONTENT_INLINE_MIME_PREFIXES = (
    'text/',
    'image/',
    'application/pdf',
    'application/json',
    'application/javascript',
    'application/xml',
)

# 允许 inline 预览的文本类扩展名（mimetypes 未识别时兑底）
_FILE_CONTENT_INLINE_TEXT_EXTS = {
    '.txt', '.md', '.markdown', '.csv', '.json', '.json5', '.yaml', '.yml',
    '.js', '.ts', '.jsx', '.tsx', '.vue', '.py', '.rb', '.go', '.rs',
    '.java', '.c', '.h', '.cpp', '.hpp', '.cs', '.php', '.swift', '.kt',
    '.sh', '.bash', '.zsh', '.ps1', '.bat', '.cmd',
    '.html', '.htm', '.css', '.scss', '.sass', '.less',
    '.xml', '.svg', '.toml', '.ini', '.cfg', '.conf', '.log', '.sql',
}


def _resolve_inline_mime(full_path: Path) -> str:
    """猜测 inline 预览用的 MIME 类型，不在白名单内的退化为 octet-stream。"""
    guessed, _ = mimetypes.guess_type(str(full_path))
    if guessed:
        if guessed.startswith(_FILE_CONTENT_INLINE_MIME_PREFIXES):
            return guessed
        return 'application/octet-stream'
    # mimetypes 未识别，按扩展名兑底
    suffix = full_path.suffix.lower()
    if suffix in _FILE_CONTENT_INLINE_TEXT_EXTS:
        return 'text/plain; charset=utf-8'
    if suffix == '.pdf':
        return 'application/pdf'
    return 'application/octet-stream'


@chat_bp.route('/api/file/content')
@api_login_required
@with_terminal
def file_content_api(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """以 inline 方式返回文件内容，用于前端预览（区别于 /api/download/file 的 attachment）。

    路径校验复用 _validate_path，与下载接口同等安全级别。
    非白名单 MIME 退化为 octet-stream，避免浏览器误执行 HTML/JS。"""
    path = (request.args.get('path') or '').strip()
    if not path:
        return jsonify({"success": False, "error": "缺少路径参数"}), 400

    valid, error, full_path = terminal.file_manager._validate_path(path)
    if not valid or full_path is None:
        return jsonify({"success": False, "error": error or "路径校验失败"}), 400
    if not full_path.exists() or not full_path.is_file():
        return jsonify({"success": False, "error": "文件不存在"}), 404

    mime_type = _resolve_inline_mime(full_path)

    # 永远不要让浏览器 inline 执行 HTML/JS（防 XSS）；
    # HTML 文件强制走下载，不 inline 预览
    if mime_type.startswith('text/html'):
        mime_type = 'application/octet-stream'

    return send_file(
        full_path,
        mimetype=mime_type,
        as_attachment=False
    )
