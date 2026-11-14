"""设置页「存储空间」接口：统计数据根占用，清理日志与旧对话备份。

仅宿主机单用户模式（host）开放：统计口径是整个数据根，多用户部署下会读到其他账号的数据，
清理更会删除他人数据，因此其它模式统一返回 ``available=false``。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from config import DATA_DIR, IS_HOST_MODE, LOGS_DIR
from modules.storage_stats import (
    clean_backups,
    clean_logs,
    is_scanning,
    load_cache,
    start_scan,
)
from server.auth_helpers import api_login_required

logger = logging.getLogger(__name__)

storage_stats_bp = Blueprint("storage_stats", __name__)

MIN_KEEP_DAYS = 1
MAX_KEEP_DAYS = 3650


def _unsupported():
    return jsonify({"success": True, "data": {"available": False, "reason": "host_only"}})


def _serialize(cache: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "available": True,
        "platform": cache.get("platform"),
        "root": cache.get("data_root"),
        "logs_root": cache.get("logs_root"),
        "scanned_at": cache.get("scanned_at"),
        "duration_ms": int(cache.get("duration_ms") or 0),
        "total_bytes": int(cache.get("total_bytes") or 0),
        "categories": cache.get("categories") or [],
        "backups": cache.get("backups") or [],
        "scanning": is_scanning(),
    }


@storage_stats_bp.route("/api/storage-stats", methods=["GET"])
@api_login_required
def get_storage_stats():
    """返回缓存快照；无缓存时立即触发后台扫描并要求前端轮询。"""
    if not IS_HOST_MODE:
        return _unsupported()
    cache = load_cache()
    if cache is None:
        start_scan()
        return jsonify({
            "success": True,
            "data": {
                "available": True,
                "platform": None,
                "root": str(Path(DATA_DIR)),
                "logs_root": str(Path(LOGS_DIR)),
                "scanned_at": None,
                "duration_ms": 0,
                "total_bytes": 0,
                "categories": [],
                "backups": [],
                "scanning": True,
            },
        })
    return jsonify({"success": True, "data": _serialize(cache)})


@storage_stats_bp.route("/api/storage-stats/refresh", methods=["POST"])
@api_login_required
def refresh_storage_stats():
    """触发后台重扫（单飞）；已有扫描在跑时 started=false。"""
    if not IS_HOST_MODE:
        return _unsupported()
    started = start_scan()
    return jsonify({"success": True, "data": {"started": started, "scanning": True}})


@storage_stats_bp.route("/api/storage-stats/clean/logs", methods=["POST"])
@api_login_required
def clean_storage_logs():
    """清空日志目录：活动日志截断，归档文件删除。"""
    if not IS_HOST_MODE:
        return _unsupported()
    try:
        result = clean_logs(Path(LOGS_DIR))
    except Exception as exc:  # noqa: BLE001 - 交给前端展示
        logger.exception("storage stats: 日志清理失败")
        return jsonify({"success": False, "error": str(exc)}), 500
    start_scan()
    return jsonify({"success": True, "data": result})


@storage_stats_bp.route("/api/storage-stats/clean/backups", methods=["POST"])
@api_login_required
def clean_storage_backups():
    """删除最后更新早于 keep_days 天的整份对话备份。"""
    if not IS_HOST_MODE:
        return _unsupported()
    body = request.get_json(silent=True) or {}
    try:
        keep_days = int(body.get("keep_days"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "invalid keep_days"}), 400
    if not MIN_KEEP_DAYS <= keep_days <= MAX_KEEP_DAYS:
        return jsonify({"success": False, "error": "keep_days out of range"}), 400
    try:
        result = clean_backups(Path(DATA_DIR) / "save", keep_days)
    except Exception as exc:  # noqa: BLE001 - 交给前端展示
        logger.exception("storage stats: 备份清理失败")
        return jsonify({"success": False, "error": str(exc)}), 500
    start_scan()
    return jsonify({"success": True, "data": result})


__all__ = ["storage_stats_bp"]
