from __future__ import annotations
import json
import time
from flask import Blueprint, jsonify
from typing import Optional

from .auth_helpers import api_login_required, get_current_username
from .context import get_or_create_usage_tracker
from .state import (
    container_manager,
    LAST_ACTIVE_FILE,
    _last_active_lock,
    _last_active_cache,
)

from modules.i18n import tr

usage_bp = Blueprint('usage', __name__)


@usage_bp.route('/api/usage', methods=['GET'])
@api_login_required
def get_usage_stats():
    """返回当前用户的模型/搜索调用统计。"""
    username = get_current_username()
    tracker = get_or_create_usage_tracker(username)
    if not tracker:
        return jsonify({"success": False, "error": tr("auth.usage_user_not_found")}), 404
    return jsonify({
        "success": True,
        "data": tracker.get_stats()
    })


def _persist_last_active_cache():
    """原子写入最近活跃时间缓存。"""
    try:
        tmp = LAST_ACTIVE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_last_active_cache, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(LAST_ACTIVE_FILE)
    except Exception:
        # 失败不阻塞主流程
        pass


def record_user_activity(username: Optional[str], ts: Optional[float] = None):
    """记录用户最近活跃时间，刷新容器 handle 并持久化。"""
    if not username:
        return
    now = ts or time.time()
    with _last_active_lock:
        _last_active_cache[username] = now
        _persist_last_active_cache()
    handle = container_manager.get_handle(username)
    if handle:
        handle.touch()

