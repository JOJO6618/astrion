from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from modules.user_manager import UserWorkspace
from modules.usage_tracker import QUOTA_DEFAULTS
from utils.conversation_manager import ConversationManager
from .context import get_or_create_usage_tracker

from config import PROJECT_MAX_STORAGE_MB, PROJECT_MAX_STORAGE_BYTES, UPLOAD_SCAN_LOG_SUBDIR, LOGS_DIR
from .state import (
    RECENT_UPLOAD_EVENT_LIMIT,
    RECENT_UPLOAD_FEED_LIMIT,
    user_manager,
    container_manager,
    get_last_active_ts,
)


def calculate_directory_size(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                for entry in iterator:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                    except (OSError, FileNotFoundError, PermissionError):
                        continue
        except (NotADirectoryError, FileNotFoundError, PermissionError, OSError):
            continue
    return total

def iso_datetime_from_epoch(epoch: Optional[float]) -> Optional[str]:
    if not epoch:
        return None
    try:
        return datetime.utcfromtimestamp(epoch).replace(microsecond=0).isoformat() + "Z"
    except (ValueError, OSError):
        return None

def compute_workspace_storage(workspace: UserWorkspace) -> Dict[str, Any]:
    project_bytes = calculate_directory_size(workspace.project_path)
    data_bytes = calculate_directory_size(workspace.data_dir)
    logs_bytes = calculate_directory_size(workspace.logs_dir)
    quarantine_bytes = calculate_directory_size(workspace.quarantine_dir)
    uploads_bytes = calculate_directory_size(workspace.uploads_dir)
    backups_bytes = calculate_directory_size(workspace.data_dir / "backups")
    usage_percent = None
    if PROJECT_MAX_STORAGE_BYTES:
        usage_percent = round(project_bytes / PROJECT_MAX_STORAGE_BYTES * 100, 2) if project_bytes else 0.0
    status = "ok"
    if usage_percent is not None:
        if usage_percent >= 95:
            status = "critical"
        elif usage_percent >= 80:
            status = "warning"
    return {
        "project_bytes": project_bytes,
        "data_bytes": data_bytes,
        "logs_bytes": logs_bytes,
        "quarantine_bytes": quarantine_bytes,
        "uploads_bytes": uploads_bytes,
        "backups_bytes": backups_bytes,
        "total_bytes": project_bytes + data_bytes + logs_bytes + quarantine_bytes,
        "limit_bytes": PROJECT_MAX_STORAGE_BYTES,
        "usage_percent": usage_percent,
        "status": status,
    }

def collect_usage_snapshot(username: str, workspace: UserWorkspace, role: Optional[str]) -> Dict[str, Any]:
    tracker = get_or_create_usage_tracker(username, workspace)
    if not tracker:
        # 兜底：理论上网页用户应始终有 tracker；这里避免因异常导致整个 dashboard 失败
        snapshot: Dict[str, Any] = {}
        for metric in ("fast", "thinking", "search"):
            default_limit = QUOTA_DEFAULTS.get("default", {}).get(metric, {}).get("limit", 0)
            snapshot[metric] = {
                "count": 0,
                "window_start": None,
                "reset_at": None,
                "limit": default_limit,
            }
        snapshot["role"] = role or "user"
        return snapshot
    stats = tracker.get_stats()
    quotas = stats.get("quotas") or {}
    windows = stats.get("windows") or {}
    snapshot: Dict[str, Any] = {}
    for metric in ("fast", "thinking", "search"):
        window_meta = windows.get(metric) or {}
        quota_meta = quotas.get(metric) or {}
        default_limit = QUOTA_DEFAULTS.get("default", {}).get(metric, {}).get("limit", 0)
        snapshot[metric] = {
            "count": int(window_meta.get("count", 0) or 0),
            "window_start": window_meta.get("window_start"),
            "reset_at": window_meta.get("reset_at") or quota_meta.get("reset_at"),
            "limit": quota_meta.get("limit", default_limit),
        }
    snapshot["role"] = role or quotas.get("role") or "user"
    return snapshot

def _read_token_totals_file(workspace: UserWorkspace) -> Dict[str, int]:
    path = workspace.data_dir / "token_totals.json"
    if not path.exists():
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            payload = json.load(fh) or {}
        input_tokens = int(payload.get("input_tokens") or payload.get("total_input_tokens") or 0)
        output_tokens = int(payload.get("output_tokens") or payload.get("total_output_tokens") or 0)
        total_tokens = int(payload.get("total_tokens") or (input_tokens + output_tokens))
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.error(f"[admin] 解析 token_totals.json 失败 ({workspace.username}): {exc}")
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

def _collect_conversation_token_totals(workspace: UserWorkspace) -> Dict[str, int]:
    try:
        manager = ConversationManager(base_dir=workspace.data_dir, project_path=str(workspace.project_path))
        stats = manager.get_statistics() or {}
        token_stats = stats.get("token_statistics") or {}
        input_tokens = int(token_stats.get("total_input_tokens") or 0)
        output_tokens = int(token_stats.get("total_output_tokens") or 0)
        total_tokens = int(token_stats.get("total_tokens") or (input_tokens + output_tokens))
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
    except Exception as exc:
        logger.error(f"[admin] 读取 legacy token 统计失败 ({workspace.username}): {exc}")
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

def collect_user_token_statistics(workspace: UserWorkspace) -> Dict[str, int]:
    """汇总单个用户在所有对话中的token累计数据。"""
    file_totals = _read_token_totals_file(workspace)
    legacy_totals = _collect_conversation_token_totals(workspace)
    return {
        "input_tokens": max(file_totals["input_tokens"], legacy_totals["input_tokens"]),
        "output_tokens": max(file_totals["output_tokens"], legacy_totals["output_tokens"]),
        "total_tokens": max(file_totals["total_tokens"], legacy_totals["total_tokens"]),
    }

def compute_usage_leaders(users: List[Dict[str, Any]], metric: str, top_n: int = 5) -> List[Dict[str, Any]]:
    ranked = sorted(
        (
            {
                "username": entry["username"],
                "count": entry.get("usage", {}).get(metric, {}).get("count", 0),
                "limit": entry.get("usage", {}).get(metric, {}).get("limit"),
            }
            for entry in users
        ),
        key=lambda item: item["count"],
        reverse=True,
    )
    return [row for row in ranked[:top_n] if row["count"]]

def collect_user_snapshots(handle_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    user_map = user_manager.list_users()
    items: List[Dict[str, Any]] = []
    role_counter: Counter = Counter()
    usage_totals = {"fast": 0, "thinking": 0, "search": 0}
    token_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    storage_total_bytes = 0
    quarantine_total_bytes = 0
    now = time.time()

    for username, record in user_map.items():
        workspace = user_manager.ensure_user_workspace(username)
        storage = compute_workspace_storage(workspace)
        usage = collect_usage_snapshot(username, workspace, record.role)
        tokens = collect_user_token_statistics(workspace)
        storage_total_bytes += storage["total_bytes"]
        quarantine_total_bytes += storage["quarantine_bytes"]
        for metric in usage_totals:
            usage_totals[metric] += usage.get(metric, {}).get("count", 0)
        for key in token_totals:
            token_totals[key] += tokens.get(key, 0)
        normalized_role = (record.role or "user").lower()
        role_counter[normalized_role] += 1
        handle = handle_map.get(username)
        handle_last = handle.get("last_active") if handle else None
        last_active = get_last_active_ts(username, handle_last)
        idle_seconds = max(0.0, now - last_active) if last_active else None
        items.append({
            "username": username,
            "email": record.email,
            "role": record.role or "user",
            "created_at": record.created_at,
            "invite_code": record.invite_code,
            "storage": storage,
            "usage": usage,
            "tokens": tokens,
            "workspace": {
                "project_path": str(workspace.project_path),
                "data_dir": str(workspace.data_dir),
                "logs_dir": str(workspace.logs_dir),
                "uploads_dir": str(workspace.uploads_dir),
            },
            "status": {
                "online": handle is not None,
                "container_mode": handle.get("mode") if handle else None,
                "last_active": iso_datetime_from_epoch(last_active),
                "idle_seconds": idle_seconds,
            },
        })

    items.sort(key=lambda entry: entry["username"])
    return {
        "items": items,
        "roles": dict(role_counter),
        "usage_totals": usage_totals,
        "token_totals": token_totals,
        "storage_total_bytes": storage_total_bytes,
        "quarantine_total_bytes": quarantine_total_bytes,
        "active_users": sum(1 for entry in items if entry["status"]["online"]),
        "total_users": len(items),
    }

def collect_container_snapshots(handle_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    cpu_values: List[float] = []
    mem_percent_values: List[float] = []
    total_mem_used = 0
    total_mem_limit = 0
    total_net_rx = 0
    total_net_tx = 0
    docker_count = 0
    failure_count = 0
    now = time.time()

    for username, handle in sorted(handle_map.items()):
        try:
            status = container_manager.get_container_status(username)
        except Exception as exc:
            status = {
                "username": username,
                "mode": handle.get("mode"),
                "error": str(exc),
                "workspace_path": handle.get("workspace_path"),
            }
        stats = status.get("stats") or {}
        state = status.get("state") or {}
        if status.get("mode") == "docker":
            docker_count += 1
        last_active = get_last_active_ts(username, handle.get("last_active"))
        idle_seconds = max(0.0, now - last_active) if last_active else None
        entry = {
            "username": username,
            "mode": status.get("mode", handle.get("mode")),
            "workspace_path": status.get("workspace_path") or handle.get("workspace_path"),
            "container_name": status.get("container_name") or handle.get("container_name"),
            "created_at": iso_datetime_from_epoch(status.get("created_at") or handle.get("created_at")),
            "last_active": iso_datetime_from_epoch(status.get("last_active") or last_active),
            "idle_seconds": idle_seconds,
            "stats": stats,
            "state": state,
            "error": status.get("error"),
        }
        if entry["error"] or (state and not state.get("running", True)):
            failure_count += 1
        mem_info = stats.get("memory") or {}
        net_info = stats.get("net_io") or {}
        cpu_val = stats.get("cpu_percent")
        mem_percent = mem_info.get("percent")
        mem_used = mem_info.get("used_bytes")
        mem_limit = mem_info.get("limit_bytes")
        rx_bytes = net_info.get("rx_bytes")
        tx_bytes = net_info.get("tx_bytes")
        if isinstance(cpu_val, (int, float)):
            cpu_values.append(cpu_val)
        if isinstance(mem_percent, (int, float)):
            mem_percent_values.append(mem_percent)
        if isinstance(mem_used, (int, float)):
            total_mem_used += mem_used
        if isinstance(mem_limit, (int, float)):
            total_mem_limit += mem_limit
        if isinstance(rx_bytes, (int, float)):
            total_net_rx += rx_bytes
        if isinstance(tx_bytes, (int, float)):
            total_net_tx += tx_bytes
        items.append(entry)

    active_total = len(handle_map)
    summary = {
        "active": active_total,
        "docker": docker_count,
        "host": active_total - docker_count,
        "issues": failure_count,
        "max_containers": container_manager.max_containers,
        "available_slots": max(0, container_manager.max_containers - active_total) if container_manager.max_containers > 0 else None,
        "avg_cpu_percent": round(sum(cpu_values) / len(cpu_values), 2) if cpu_values else None,
        "avg_mem_percent": round(sum(mem_percent_values) / len(mem_percent_values), 2) if mem_percent_values else None,
        "total_mem_used_bytes": total_mem_used,
        "total_mem_limit_bytes": total_mem_limit,
        "net_rx_bytes": total_net_rx,
        "net_tx_bytes": total_net_tx,
    }
    return {"items": items, "summary": summary}

def parse_upload_line(line: str) -> Optional[Dict[str, Any]]:
    marker = "UPLOAD_AUDIT "
    idx = line.find(marker)
    if idx == -1:
        return None
    payload = line[idx + len(marker):].strip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    timestamp_value = data.get("timestamp")
    timestamp_dt = None
    if isinstance(timestamp_value, str):
        try:
            timestamp_dt = datetime.fromisoformat(timestamp_value)
        except ValueError:
            timestamp_dt = None
    data["_dt"] = timestamp_dt
    return data

def collect_upload_events(limit: int = RECENT_UPLOAD_EVENT_LIMIT) -> List[Dict[str, Any]]:
    base_dir = (Path(LOGS_DIR).expanduser().resolve() / UPLOAD_SCAN_LOG_SUBDIR).resolve()
    events: List[Dict[str, Any]] = []
    if not base_dir.exists():
        return []
    for log_file in sorted(base_dir.glob('*.log')):
        buffer: deque = deque(maxlen=limit)
        try:
            with open(log_file, 'r', encoding='utf-8') as fh:
                for line in fh:
                    if 'UPLOAD_AUDIT' not in line:
                        continue
                    buffer.append(line.strip())
        except OSError:
            continue
        for raw in buffer:
            event = parse_upload_line(raw)
            if event:
                events.append(event)
    events.sort(key=lambda item: item.get('_dt') or datetime.min, reverse=True)
    return events[:limit]

def summarize_upload_events(events: List[Dict[str, Any]], quarantine_total_bytes: int) -> Dict[str, Any]:
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=24)
    last_24h = [evt for evt in events if evt.get('_dt') and evt['_dt'] >= cutoff]
    accepted_24h = sum(1 for evt in last_24h if evt.get('accepted'))
    blocked_24h = len(last_24h) - accepted_24h
    skipped_24h = sum(1 for evt in last_24h if ((evt.get('scan') or {}).get('status') == 'skipped'))
    source_counter = Counter((evt.get('source') or 'unknown') for evt in events)
    sanitized_events: List[Dict[str, Any]] = []
    for evt in events[:RECENT_UPLOAD_FEED_LIMIT]:
        sanitized_events.append({k: v for k, v in evt.items() if k != '_dt'})
    return {
        "stats": {
            "total_tracked": len(events),
            "last_24h": len(last_24h),
            "accepted_last_24h": accepted_24h,
            "blocked_last_24h": blocked_24h,
            "skipped_scan_last_24h": skipped_24h,
            "quarantine_bytes": quarantine_total_bytes,
        },
        "recent_events": sanitized_events,
        "sources": [{"source": src, "count": count} for src, count in source_counter.most_common()],
    }

def summarize_invite_codes(codes: List[Dict[str, Any]]) -> Dict[str, int]:
    active = consumed = unlimited = 0
    for code in codes:
        remaining = code.get('remaining')
        if remaining is None:
            unlimited += 1
        elif remaining > 0:
            active += 1
        else:
            consumed += 1
    return {
        "total": len(codes),
        "active": active,
        "consumed": consumed,
        "unlimited": unlimited,
    }

def build_admin_dashboard_snapshot() -> Dict[str, Any]:
    handle_map = container_manager.list_containers()
    user_data = collect_user_snapshots(handle_map)
    container_data = collect_container_snapshots(handle_map)
    invite_codes = user_manager.list_invite_codes()
    upload_events = collect_upload_events()
    uploads_summary = summarize_upload_events(upload_events, user_data['quarantine_total_bytes'])
    overview = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "totals": {
            "users": user_data['total_users'],
            "active_users": user_data['active_users'],
            "containers_active": container_data['summary']['active'],
            "containers_max": container_data['summary']['max_containers'],
            "available_container_slots": container_data['summary']['available_slots'],
        },
        "roles": user_data['roles'],
        "usage_totals": user_data['usage_totals'],
        "token_totals": user_data['token_totals'],
        "usage_leaders": {
            metric: compute_usage_leaders(user_data['items'], metric)
            for metric in ("fast", "thinking", "search")
        },
        "storage": {
            "total_bytes": user_data['storage_total_bytes'],
            "per_user_limit_bytes": PROJECT_MAX_STORAGE_BYTES,
            "project_max_mb": PROJECT_MAX_STORAGE_MB,
            "warning_users": [
                {
                    "username": entry['username'],
                    "usage_percent": entry['storage']['usage_percent'],
                    "status": entry['storage']['status'],
                }
                for entry in user_data['items']
                if entry['storage']['status'] != 'ok'
            ],
        },
        "containers": container_data['summary'],
        "invites": summarize_invite_codes(invite_codes),
        "uploads": uploads_summary['stats'],
    }
    return {
        "generated_at": overview['generated_at'],
        "overview": overview,
        "users": user_data['items'],
        "containers": container_data['items'],
        "invites": {
            "summary": summarize_invite_codes(invite_codes),
            "codes": invite_codes,
        },
        "uploads": uploads_summary,
    }
