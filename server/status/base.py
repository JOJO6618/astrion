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
from server.context import with_terminal, attach_user_broadcast
from server.state import (
    PROJECT_STORAGE_CACHE,
    PROJECT_STORAGE_CACHE_TTL_SECONDS,
    PROJECT_MAX_STORAGE_MB,
    container_manager,
    user_manager,
)
from config import AGENT_VERSION, TERMINAL_SANDBOX_MODE, TERMINAL_SANDBOX_MOUNT_PATH
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

def _is_docker_project_request() -> bool:
    return bool(session.get("username")) and not bool(session.get("host_mode")) and not bool(session.get("is_api_user"))

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
@with_terminal
def get_status(terminal, workspace, username):
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
        status = terminal.get_status()
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
    if terminal.terminal_manager:
        status['terminals'] = terminal.terminal_manager.list_terminals()
    timings["terminals_ms"] = (time.perf_counter() - phase_started_at) * 1000
    phase_started_at = time.perf_counter()
    try:
        current_conv = terminal.context_manager.current_conversation_id
        status.setdefault('conversation', {})['current_id'] = current_conv
        if current_conv and not current_conv.startswith('temp_'):
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
    # docker/web 多用户模式下，宿主绝对路径会泄露服务器目录布局与系统用户名，
    # 对用户无实际意义（操作均在容器 /workspace 内），脱敏为容器视角路径。
    if _is_host_mode_request():
        status['project_path'] = str(workspace.project_path)
    else:
        status['project_path'] = TERMINAL_SANDBOX_MOUNT_PATH
    phase_started_at = time.perf_counter()
    try:
        # 首屏状态只需要容器是否存在/运行等轻量信息；Docker stats 很慢，
        # 由 /api/container-status 在用量面板需要时单独拉取。
        workspace_id = getattr(workspace, 'workspace_id', None) or session.get('workspace_id') or 'default'
        container_key = (
            f"host::{workspace_id}"
            if bool(session.get("host_mode")) or username == "host"
            else f"{username}::{workspace_id}"
        )
        status['container'] = container_manager.get_container_status(container_key, include_stats=False)
    except Exception as exc:
        status['container'] = {"success": False, "error": str(exc)}
        log_conn_diag(f"status container-status-failed user={username} error={exc}")
    timings["container_ms"] = (time.perf_counter() - phase_started_at) * 1000
    status['version'] = AGENT_VERSION
    phase_started_at = time.perf_counter()
    try:
        policy = resolve_admin_policy(user_manager.get_user(username))
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

@status_bp.route('/api/container-status')
@api_login_required
@with_terminal
def get_container_status_api(terminal, workspace, username):
    try:
        container_key = (
            f"host::{getattr(workspace, 'workspace_id', None) or session.get('workspace_id') or 'default'}"
            if bool(session.get("host_mode")) or username == "host"
            else f"{username}::{getattr(workspace, 'workspace_id', None) or session.get('workspace_id') or 'default'}"
        )
        status = container_manager.get_container_status(container_key)
        return jsonify({"success": True, "data": status})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

@status_bp.route('/api/project-storage')
@api_login_required
@with_terminal
def get_project_storage(terminal, workspace, username):
    now = time.time()
    workspace_id = getattr(workspace, "workspace_id", None) or session.get("workspace_id") or "default"
    cache_key = f"{username}::{workspace_id}"
    cache_entry = PROJECT_STORAGE_CACHE.get(cache_key)
    if cache_entry and (now - cache_entry.get("ts", 0)) < PROJECT_STORAGE_CACHE_TTL_SECONDS:
        return jsonify({"success": True, "data": cache_entry["data"]})
    try:
        file_manager = getattr(terminal, 'file_manager', None)
        if not file_manager:
            return jsonify({"success": False, "error": tr("status_base.file_manager_not_initialized")}), 500
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
