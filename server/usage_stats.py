"""设置页「用量统计」接口：按当前账号汇总全部工作区与 Chat 的费用账本。

只读：不创建 WebTerminal／容器、不选择工作区、不写账本；仅维护可重建的派生缓存
（modules/usage_stats/cache.py）。工作区与 Chat 的数据目录均经可信身份解析。
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta, timezone, tzinfo
from pathlib import Path
from typing import List, Optional, Tuple

from flask import Blueprint, jsonify, request

from config import TERMINAL_SANDBOX_MODE
from modules.usage_stats import WorkspaceRef, build_report, gather_sources
from server import state
from server.auth_helpers import api_login_required, get_current_username

logger = logging.getLogger(__name__)

usage_stats_bp = Blueprint("usage_stats", __name__)

_TZ_NAME = re.compile(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+){0,2}$")


def _resolve_timezone() -> Tuple[tzinfo, str]:
    """优先 IANA 时区名；系统缺时区库（如 Windows 无 tzdata）时回退浏览器给出的 UTC 偏移。"""
    name = str(request.args.get("tz") or "").strip()
    if name and _TZ_NAME.match(name):
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name), name
        except Exception:
            pass
    try:
        offset = int(request.args.get("tz_offset") or 0)  # 分钟，东区为正
    except (TypeError, ValueError):
        offset = 0
    offset = max(-14 * 60, min(14 * 60, offset))
    return timezone(timedelta(minutes=offset)), f"UTC{offset // 60:+03d}:{abs(offset) % 60:02d}"


def _workspace_refs(username: str, identity) -> Tuple[List[WorkspaceRef], bool]:
    """列出当前账号的全部 Agent 工作区；返回 (引用列表, 账本目录是否共享)。"""
    from server.context.data import get_workspace_data

    host = identity.host_mode and (TERMINAL_SANDBOX_MODE or "host").lower() == "host"
    if host:
        from modules.host_workspace_manager import load_host_workspace_catalog
        entries = [(item.get("workspace_id"), item.get("label")) for item in
                   load_host_workspace_catalog().get("workspaces") or []]
    elif identity.is_api_user:
        entries = [(ws_id, ws_id) for ws_id in state.api_user_manager.list_workspaces(username)]
    else:
        listing = state.user_manager.list_user_workspaces(username, prepare=False)
        entries = [(ws_id, item.get("label")) for ws_id, item in listing.items()]

    refs: List[WorkspaceRef] = []
    for ws_id, label in entries:
        if not ws_id:
            continue
        try:
            data = get_workspace_data(username, ws_id, identity=identity)
            refs.append(WorkspaceRef(
                workspace_id=str(ws_id), label=str(label or ws_id),
                data_dir=Path(data.workspace.data_dir), conversations_dir=Path(data.manager().conversations_dir),
            ))
        except Exception:
            # 单个工作区不可解析（目录缺失／配置损坏）不影响其余统计
            logger.warning("usage stats: skip workspace %s", ws_id, exc_info=True)
    return refs, host


def _chat_root() -> Optional[Path]:
    try:
        from server.chat_mode.context import principal
        from server.chat_mode_scope import scope_for_principal
        return Path(scope_for_principal(principal()).root)
    except Exception:
        logger.warning("usage stats: chat scope unavailable", exc_info=True)
        return None


@usage_stats_bp.route("/api/usage-stats", methods=["GET"])
@api_login_required
def get_usage_stats():
    from modules.exchange_rates import get_rate
    from server.context.workspace import request_identity

    username = get_current_username()
    if not username:
        return jsonify({"success": False, "error": "unauthorized"}), 401
    tz, tz_label = _resolve_timezone()
    selected = str(request.args.get("source") or "all").strip() or "all"
    try:
        refs, shared = _workspace_refs(username, request_identity())
        sources = gather_sources(refs, _chat_root(), shared_ledger=shared)
    except Exception as exc:
        logger.exception("usage stats: gather failed")
        return jsonify({"success": False, "error": str(exc)}), 500

    chosen = sources if selected == "all" else [item for item in sources if item.id == selected]
    if selected != "all" and not chosen:
        return jsonify({"success": False, "error": "unknown source"}), 400
    report = build_report(chosen, tz=tz)
    report.update({
        "selected": selected,
        "timezone": tz_label,
        "sources": [{"id": item.id, "label": item.label, "mode": item.mode,
                     "has_data": bool(item.conversations)} for item in sources],
        "exchange_rate": get_rate(),
    })
    return jsonify({"success": True, "data": report})
