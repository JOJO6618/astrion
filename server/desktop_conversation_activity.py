"""Read-only conversation activity for the desktop shell and tab strip."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from modules.desktop_navigation.model import conversation_id

ACTIVE_TASK_STATUSES = {"pending", "running", "cancel_requested"}


def task_scope(record) -> tuple[str, str, str]:
    return (
        record.principal.conversation_kind,
        record.workspace_id or "",
        conversation_id(record.conversation_id),
    )


def pending_conversation_requests(
    username: str, records: Iterable[Any] | None = None,
) -> dict[tuple[str, str, str], dict[str, set[str]]]:
    """Bind requests to their live task, including when a worktree is in use."""
    from server.runtime import runtime_service
    from server.tasks import task_manager

    pending = runtime_service.list_pending_approvals(username)
    if not any(pending.values()):
        return {}
    records = task_manager.list_tasks(username) if records is None else records
    tasks = {record.task_id: record for record in records if record.status in ACTIVE_TASK_STATUSES}
    result: dict[tuple[str, str, str], dict[str, set[str]]] = {}
    for source, rows in pending.items():
        for row in rows:
            if source == "tool" and (
                not row.get("human_approval_required", not row.get("auto_review_required"))
                or row.get("human_decision")
            ):
                continue
            record = tasks.get(row.get("task_id"))
            if record is None or not record.conversation_id or not row.get("conversation_id"):
                continue
            scope = task_scope(record)
            if conversation_id(row["conversation_id"]) != scope[2]:
                continue
            if source == "question":
                kind, request_id = "question", row.get("batch_id") or row.get("question_id")
            else:
                kind, request_id = "approval", row.get("approval_id")
            if request_id:
                result.setdefault(scope, {}).setdefault(kind, set()).add(request_id)
    return result


def index_titles(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        cid: str(item.get("title") or "").strip()
        for cid, item in data.items() if isinstance(item, dict) and item.get("title")
    }


def conversation_activity(username: str, is_running, titles_for_workspace) -> list[dict]:
    """Group retained task records without creating runtimes or loading histories."""
    from modules.host_workspace_manager import load_host_workspace_catalog
    from server.chat_mode.context import resources
    from server.tasks import task_manager

    records = task_manager.list_tasks(username)
    groups = defaultdict(list)
    active = set()
    for record in records:
        if not record.conversation_id:
            continue
        scope = task_scope(record)
        groups[scope].append(record)
        if record.status in ACTIVE_TASK_STATUSES:
            active.add(scope)
    if not groups:
        return []

    pending = pending_conversation_requests(username, records)
    workspaces = {workspace_id for _, workspace_id, _ in groups if workspace_id}
    catalog = load_host_workspace_catalog().get("workspaces", []) if workspaces else []
    labels = {
        item["workspace_id"]: item.get("label") or item["workspace_id"]
        for item in catalog if item["workspace_id"] in workspaces
    }
    titles = {workspace_id: titles_for_workspace(workspace_id) for workspace_id in labels}
    if any(kind == "chat" for kind, _, _ in groups):
        _, _, chat = resources()
        titles[""] = index_titles(chat.manager.index_file)

    # Use the same runtime predicate as the tab strip, independently of which tabs are open.
    active_agent = {(workspace_id, cid) for kind, workspace_id, cid in active if kind == "agent"}
    rows = []
    for scope, candidates in groups.items():
        kind, workspace_id, cid = scope
        if kind == "agent" and workspace_id not in labels:
            continue
        latest = max((r for r in candidates if r.task_type != "compression"),
                     key=lambda r: r.created_at, default=None)
        requests = pending.get(scope, {})
        running = scope in active or (kind == "agent" and is_running(username, workspace_id, cid, active_agent))
        rows.append({
            "conversation_kind": kind,
            "workspace_id": workspace_id,
            "conversation_id": cid,
            "workspace_label": labels.get(workspace_id, ""),
            "title": str(titles[workspace_id].get(cid) or "")[:200],
            "run_id": latest.task_id if latest else None,
            "run_status": latest.status if latest else None,
            "running": running,
            "pending": {key: sorted(ids) for key, ids in requests.items()},
        })
    return rows
