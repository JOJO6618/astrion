from __future__ import annotations
from server.status import status_bp
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
from server.context import with_terminal
from server.context.data_decorators import with_workspace_data
from server.context.status_view import workspace_status
from server.state import (
    PROJECT_STORAGE_CACHE,
    PROJECT_STORAGE_CACHE_TTL_SECONDS,
    PROJECT_MAX_STORAGE_MB,
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

STATUS_DIAG_VERBOSE = os.environ.get("STATUS_DIAG_VERBOSE", "0") not in {"0", "false", "False"}
STATUS_DIAG_SLOW_MS = int(os.environ.get("STATUS_DIAG_SLOW_MS", "1200") or 1200)


def _close_terminal_for_key(term_key: str):
    terminal = state.user_terminals.pop(term_key, None)
    if terminal:
        try:
            if getattr(terminal, "terminal_manager", None):
                terminal.terminal_manager.close_all()
        except Exception:
            pass

def _is_host_mode_request() -> bool:
    return bool(session.get("host_mode")) and (TERMINAL_SANDBOX_MODE or "").lower() == "host"

def _active_task_counts(username: str) -> dict:
    running_by_workspace = {}
    try:
        from server.tasks import task_manager
        for rec in task_manager.list_tasks(username):
            if rec.status in {"pending", "running", "cancel_requested"}:
                ws_id = getattr(rec, "workspace_id", None) or "default"
                running_by_workspace[ws_id] = running_by_workspace.get(ws_id, 0) + 1
    except Exception:
        running_by_workspace = {}
    return running_by_workspace

@status_bp.route('/api/health')
@api_login_required
def get_health():
    """轻量健康检查：用于前端连接心跳，避免触发重型 status 计算。"""
    started_at = time.perf_counter()
    heartbeat_id = request.headers.get("X-Connection-Heartbeat", "").strip()
    username = session.get("username") or "-"
    payload = {
        "success": True,
        "status": "ok",
        "server_time_ms": int(time.time() * 1000),
    }
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    if request.args.get("diag") == "1" or elapsed_ms >= 800:
        log_conn_diag(
            f"health hb={heartbeat_id or '-'} user={username} "
            f"elapsed_ms={elapsed_ms:.1f} host_mode={bool(session.get('host_mode'))} "
            f"workspace_id={session.get('workspace_id') or '-'}"
        )
    return jsonify(payload)

@status_bp.route('/api/status')
@api_login_required
@with_workspace_data
def get_status(data_context, workspace, username):
    """获取系统状态（包含对话、容器、版本等信息）"""
    started_at = time.perf_counter()
    heartbeat_id = request.headers.get("X-Connection-Heartbeat", "").strip()
    diag_requested = STATUS_DIAG_VERBOSE or request.args.get("diag") == "1"
    timings = {
        "terminal_ms": 0.0,
        "terminals_ms": 0.0,
        "conversation_meta_ms": 0.0,
        "container_ms": 0.0,
        "policy_ms": 0.0,
    }
    phase_started_at = time.perf_counter()
    try:
        status, terminal = workspace_status(data_context, request.args.get('conversation_id'))
    except Exception as exc:
        timings["terminal_ms"] = (time.perf_counter() - phase_started_at) * 1000
        total_ms = (time.perf_counter() - started_at) * 1000
        log_conn_diag(
            f"status hb={heartbeat_id or '-'} user={username} phase=terminal.get_status "
            f"total_ms={total_ms:.1f} terminal_ms={timings['terminal_ms']:.1f} error={exc}"
        )
        raise
    timings["terminal_ms"] = (time.perf_counter() - phase_started_at) * 1000
    phase_started_at = time.perf_counter()
    if terminal is not None and terminal.terminal_manager:
        status['terminals'] = terminal.terminal_manager.list_terminals()
    timings["terminals_ms"] = (time.perf_counter() - phase_started_at) * 1000
    phase_started_at = time.perf_counter()
    try:
        current_conv = terminal.context_manager.current_conversation_id if terminal else None
        if terminal:
            status.setdefault('conversation', {})['current_id'] = current_conv
        if terminal and current_conv and not current_conv.startswith('temp_'):
            current_conv_data = terminal.context_manager._get_conversation_manager_for_id(current_conv).load_conversation(current_conv)
            if current_conv_data:
                status['conversation']['title'] = current_conv_data.get('title', '未知对话')
                status['conversation']['created_at'] = current_conv_data.get('created_at')
                status['conversation']['updated_at'] = current_conv_data.get('updated_at')
                meta = current_conv_data.get("metadata", {}) or {}
                # 压缩标记是持久化的，进程在压缩中途被杀会残留 True（压缩随进程
                # 死亡），这里按 pid 懒清理，避免前端误判压缩中锁死输入栏。
                from ..deep_compression import heal_stale_compression_flag
                compression_in_progress = heal_stale_compression_flag(
                    terminal.context_manager._get_conversation_manager_for_id(current_conv),
                    current_conv,
                    meta,
                    context_manager=terminal.context_manager,
                )
                status['conversation']['compression'] = {
                    "in_progress": compression_in_progress,
                    "mode": meta.get("compression_mode") if compression_in_progress else None,
                    "stage": meta.get("compression_stage") if compression_in_progress else None,
                    "error": meta.get("compression_error"),
                    "count": int(meta.get("compression_count", 0) or 0),
                    "job_id": meta.get("compression_job_id") if compression_in_progress else None,
                }
    except Exception as exc:
        log_conn_diag(f"status conversation-meta-failed user={username} error={exc}")
    timings["conversation_meta_ms"] = (time.perf_counter() - phase_started_at) * 1000
    # Host 单机形态：始终返回真实工作区路径（容器 /workspace 视角已随 Docker 版移出）
    status['project_path'] = str(workspace.project_path)
    phase_started_at = time.perf_counter()
    # 主仓为 Host 单机形态：不再查询 Docker 运行时，container 字段仅保留
    # `mode: "host"` 这一旧形状，供尚未迁移的消费者区分 Host 与容器模式。
    workspace_id = getattr(workspace, 'workspace_id', None) or session.get('workspace_id') or 'default'
    status['container'] = {"success": True, "mode": "host", "workspace_id": workspace_id}
    status['workspace_id'] = data_context.workspace_id
    timings["container_ms"] = (time.perf_counter() - phase_started_at) * 1000
    status['version'] = AGENT_VERSION
    phase_started_at = time.perf_counter()
    try:
        policy = resolve_admin_policy()
        status['admin_policy'] = {
            "ui_blocks": policy.get("ui_blocks") or {},
            "disabled_models": policy.get("disabled_models") or [],
            "forced_category_states": policy.get("forced_category_states") or {},
            "version": policy.get("updated_at"),
        }
    except Exception:
        pass
    timings["policy_ms"] = (time.perf_counter() - phase_started_at) * 1000
    total_ms = (time.perf_counter() - started_at) * 1000
    is_slow = total_ms >= STATUS_DIAG_SLOW_MS
    if diag_requested or is_slow:
        conversation = status.get("conversation") or {}
        log_conn_diag(
            f"status hb={heartbeat_id or '-'} user={username} host_mode={bool(session.get('host_mode'))} "
            f"workspace_id={session.get('workspace_id') or '-'} "
            f"conversation_id={conversation.get('current_id') or '-'} "
            f"total_ms={total_ms:.1f} slow={is_slow} "
            f"terminal_ms={timings['terminal_ms']:.1f} terminals_ms={timings['terminals_ms']:.1f} "
            f"conversation_meta_ms={timings['conversation_meta_ms']:.1f} "
            f"container_ms={timings['container_ms']:.1f} policy_ms={timings['policy_ms']:.1f}"
        )
    return jsonify(status)

@status_bp.route('/api/project-storage')
@api_login_required
@with_workspace_data
def get_project_storage(data_context, workspace, username):
    now = time.time()
    workspace_id = getattr(workspace, "workspace_id", None) or session.get("workspace_id") or "default"
    cache_key = f"{username}::{workspace_id}"
    cache_entry = PROJECT_STORAGE_CACHE.get(cache_key)
    if cache_entry and (now - cache_entry.get("ts", 0)) < PROJECT_STORAGE_CACHE_TTL_SECONDS:
        return jsonify({"success": True, "data": cache_entry["data"]})
    try:
        from modules.file_manager import FileManager
        from server.context.resources import find_runtime
        terminal = find_runtime(username, workspace)
        file_manager = getattr(terminal, 'file_manager', None) if terminal else None
        if file_manager is None:
            file_manager = FileManager(str(workspace.project_path), data_dir=str(workspace.data_dir))
        used_bytes = file_manager._get_project_size()
        limit_bytes = PROJECT_MAX_STORAGE_MB * 1024 * 1024 if PROJECT_MAX_STORAGE_MB else None
        usage_percent = (used_bytes / limit_bytes * 100) if limit_bytes else None
        data = {
            "used_bytes": used_bytes,
            "limit_bytes": limit_bytes,
            "limit_label": f"{PROJECT_MAX_STORAGE_MB}MB" if PROJECT_MAX_STORAGE_MB else "未限制",
            "usage_percent": usage_percent
        }
        PROJECT_STORAGE_CACHE[cache_key] = {"ts": now, "data": data}
        return jsonify({"success": True, "data": data})
    except Exception as exc:
        stale = PROJECT_STORAGE_CACHE.get(cache_key)
        if stale:
            return jsonify({"success": True, "data": stale.get("data"), "stale": True}), 200
        return jsonify({"success": False, "error": str(exc)}), 500
