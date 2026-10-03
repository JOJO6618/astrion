"""个人指令拦截设置，Web 会话与本机 Bearer 客户端共用。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import jsonify, request, session

from config import DATA_DIR
from modules.command_blocking import (
    get_recommended_rules,
    load_command_blocking_config,
    save_command_blocking_config,
)
from server.chat import chat_bp
from server.context import with_terminal
from server.gateway_auth import api_login_or_host_token_required
from server.security import rate_limited


def _data_directory(workspace: Any, username: str) -> Path:
    if workspace is not None:
        return Path(workspace.data_dir)
    if session.get("host_mode"):
        # 本机免登录通道的个人配置与现有 personalization 一致。
        return Path(DATA_DIR).expanduser().resolve()
    from server.state import user_manager

    # 新用户尚未创建工作区时，也不得回退到其他用户共享的 DATA_DIR。
    return user_manager._personal_root(username) / "personalization"


def _response(config: dict):
    return jsonify(success=True, **config, recommended_rules=get_recommended_rules())


@chat_bp.route("/api/command-blocking", methods=["GET"])
@api_login_or_host_token_required
@with_terminal(allow_no_workspace=True)
def get_command_blocking(terminal: Any, workspace: Any, username: str):
    """只读取当前身份的个人规则，不要求管理员角色。"""
    try:
        return _response(load_command_blocking_config(_data_directory(workspace, username)))
    except (OSError, ValueError, KeyError) as exc:
        return jsonify(success=False, error=str(exc)), 500


@chat_bp.route("/api/command-blocking", methods=["POST"])
@api_login_or_host_token_required
@with_terminal(allow_no_workspace=True)
@rate_limited("command_blocking_update", 30, 60, scope="user")
def update_command_blocking(terminal: Any, workspace: Any, username: str):
    """部分更新个人开关或规则；调用方不能指定其他用户或存储路径。"""
    payload = request.get_json(silent=True)
    try:
        config = save_command_blocking_config(_data_directory(workspace, username), payload)
        return _response(config)
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except (OSError, KeyError) as exc:
        return jsonify(success=False, error=str(exc)), 500
