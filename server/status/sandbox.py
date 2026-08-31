# server/status/sandbox.py - Windows WSL 沙箱环境检测与一键安装 API
#
# - GET  /api/sandbox/status         分级检测沙箱状态（前端进页面自动调用）
# - POST /api/sandbox/setup          启动一键安装（后台线程跑 setup-wsl-sandbox.ps1）
# - GET  /api/sandbox/setup/status   轮询安装进度（阶段 / 步骤 / 日志 / 下载量）
#
# 仅 Windows + 宿主机模式（TERMINAL_SANDBOX_MODE=host 且会话 host_mode）适用；
# 其它环境 applicable=false，前端不展示任何提示。
# 安装进程由 server 直接在宿主机拉起（沙箱尚未建立的"鸡生蛋"场景，
# 由用户在前端显式点击触发，不经 run_command 沙箱链路）。

from __future__ import annotations

from flask import jsonify, request, session

from server.status import status_bp
from server.auth_helpers import api_login_required
from modules.sandbox_setup_manager import sandbox_setup_manager
from modules.i18n import tr


def _is_host_mode_request() -> bool:
    try:
        from config import TERMINAL_SANDBOX_MODE
    except Exception:
        return False
    return bool(session.get("host_mode")) and (TERMINAL_SANDBOX_MODE or "").lower() == "host"


@status_bp.route('/api/sandbox/status')
@api_login_required
def get_sandbox_status():
    """分级检测沙箱状态。force=1 时绕过短缓存实测。"""
    if not _is_host_mode_request():
        return jsonify({"success": True, "data": {"applicable": False, "state": "not_applicable"}})
    force = request.args.get("force") == "1"
    data = sandbox_setup_manager.get_sandbox_status(force=force)
    return jsonify({"success": True, "data": data})


@status_bp.route('/api/sandbox/setup', methods=['POST'])
@api_login_required
def start_sandbox_setup():
    """启动一键安装。body: {"enable_wsl_if_needed": bool}"""
    if not _is_host_mode_request():
        return jsonify({"success": False, "error": tr("sandbox.setup_not_host_mode")}), 400
    payload = request.get_json(silent=True) or {}
    result = sandbox_setup_manager.start_setup(bool(payload.get("enable_wsl_if_needed")))
    if not result["started"]:
        return jsonify({"success": False, "error": result["error"]}), 409
    return jsonify({"success": True, "data": sandbox_setup_manager.get_setup_progress()})


@status_bp.route('/api/sandbox/setup/status')
@api_login_required
def get_sandbox_setup_status():
    """轮询安装进度。"""
    return jsonify({"success": True, "data": sandbox_setup_manager.get_setup_progress()})
