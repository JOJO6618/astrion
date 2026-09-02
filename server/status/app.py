from __future__ import annotations
from server.status import status_bp
from server.status.docker import (
    _parse_android_version_from_gradle,
    _resolve_android_apk_path,
    _load_app_changelog,
)
import time
import re
import os
import json
import shutil
import subprocess
import sys
import tempfile
import plistlib
from pathlib import Path
from flask import Blueprint, jsonify, request, send_file, session

from server.auth_helpers import api_login_required, resolve_admin_policy
from server.context import with_terminal, attach_user_broadcast
from server.state import (
    PROJECT_STORAGE_CACHE,
    PROJECT_STORAGE_CACHE_TTL_SECONDS,
    PROJECT_MAX_STORAGE_MB,
    container_manager,
    user_manager,
)
from config import AGENT_VERSION, TERMINAL_SANDBOX_MODE
from modules.host_workspace_manager import (
    create_host_workspace,
    delete_host_workspace,
    load_host_workspace_catalog,
    rename_host_workspace,
    resolve_host_workspace,
    set_default_host_workspace,
)
from utils.host_workspace_debug import write_host_workspace_debug
from server.utils_common import log_conn_diag
import server.state as state
from modules.i18n import tr
@status_bp.route('/api/app/version')
@api_login_required
def get_app_version_info():
    project_root = Path(__file__).resolve().parent.parent.parent
    version_code, version_name = _parse_android_version_from_gradle(project_root)
    apk_path = _resolve_android_apk_path(project_root)
    apk_exists = apk_path.exists()
    file_size = apk_path.stat().st_size if apk_exists else 0
    published_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(apk_path.stat().st_mtime)) if apk_exists else None
    changelog = _load_app_changelog(project_root)
    apk_url = request.url_root.rstrip("/") + "/api/app/apk/latest"

    current_code_raw = request.args.get("currentVersionCode", "").strip()
    current_code = int(current_code_raw) if current_code_raw.isdigit() else None
    has_update = (version_code > current_code) if current_code is not None else None

    return jsonify({
        "success": True,
        "data": {
            "latestVersionCode": version_code,
            "latestVersionName": version_name,
            "apkUrl": apk_url,
            "apkSha256": "",
            "fileSizeBytes": file_size,
            "changelog": changelog,
            "forceUpdate": False,
            "publishedAt": published_at,
            "minSupportedVersionCode": 1,
            "apkExists": apk_exists,
            "hasUpdate": has_update,
        }
    })

@status_bp.route('/api/app/apk/latest')
def download_latest_apk():
    # 安全：未认证全量下载 130MB+ 文件是带宽放大攻击面（2026-09-02 审计）；
    # 与 /api/app/version 一致要求登录，并加来源限流。
    from server.auth_helpers import is_logged_in
    if not is_logged_in():
        return jsonify({"success": False, "error": tr("auth.not_logged_in")}), 401
    from server.security import check_rate_limit, get_client_ip
    limited, retry_after = check_rate_limit("apk_download", 10, 60, get_client_ip())
    if limited:
        return jsonify({"success": False, "error": "rate_limited", "retry_after": retry_after}), 429
    project_root = Path(__file__).resolve().parent.parent.parent
    apk_path = _resolve_android_apk_path(project_root)
    if not apk_path.exists():
        return jsonify({"success": False, "error": tr("status_app.apk_not_found")}), 404
    return send_file(
        apk_path,
        as_attachment=True,
        download_name="cyj-agent-latest.apk",
        mimetype="application/vnd.android.package-archive",
        conditional=False,
        etag=False,
        max_age=0,
    )
