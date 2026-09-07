"""上传相关的管理器获取与错误响应构造。"""
from __future__ import annotations

from flask import jsonify

from modules.gui_file_manager import GuiFileManager
from modules.upload_security import UploadQuarantineManager, UploadSecurityError


def get_gui_manager(workspace):
    return GuiFileManager(str(workspace.project_path))


def get_upload_guard(workspace):
    return UploadQuarantineManager(workspace)


def build_upload_error_response(exc: UploadSecurityError):
    status = 400
    if exc.code in {"scanner_missing", "scanner_unavailable"}:
        status = 500
    return jsonify({
        "success": False,
        "error": str(exc),
        "code": exc.code,
    }), status
