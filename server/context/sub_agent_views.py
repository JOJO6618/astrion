"""Persisted sub-agent view for conversations without an execution runtime."""
from __future__ import annotations

import json
from pathlib import Path

from modules.sub_agent.stats import SubAgentStatsMixin
from modules.sub_agent.state import TERMINAL_STATUSES
from server.context.background import persisted_sub_agents


def stored_overview(data_context, conversation_id):
    if not conversation_id:
        return []
    saved = persisted_sub_agents(data_context)
    conversation = data_context.manager_for(conversation_id).read_conversation(conversation_id) or {}
    announced = {(message.get("metadata") or {}).get("task_id")
                 for message in conversation.get("messages", [])
                 if (message.get("metadata") or {}).get("sub_agent_notice")}
    formatter = SubAgentStatsMixin()
    rows = []
    for task_id, task in (saved.get("tasks") or {}).items():
        if task.get("conversation_id") != conversation_id or task.get("multi_agent_mode"):
            continue
        row = {key: task.get(key) for key in (
            "agent_id", "summary", "status", "created_at", "updated_at", "last_tool",
            "deliverables_dir", "copied_path", "conversation_id", "sub_conversation_id")}
        row.update(task_id=task_id, display_name=task.get("display_name") or "",
                   target_dir=task.get("target_project_dir"), current_context_tokens=0,
                   run_in_background=bool(task.get("run_in_background")))
        if task.get("stats_file"):
            try:
                stats = json.loads(Path(task["stats_file"]).read_text(encoding="utf-8"))
                row["current_context_tokens"] = stats.get("current_context_tokens", 0)
                row["stats_summary"] = formatter._build_stats_summary(stats)
            except (OSError, ValueError):
                pass
        terminal = task.get("status") in TERMINAL_STATUSES.union({"terminated"})
        if terminal:
            final = task.get("final_result") or {}
            row.update(final_message=final.get("system_message") or final.get("message"),
                       success=final.get("success"))
        row["notice_pending"] = bool(terminal and row["run_in_background"]
                                     and not task.get("notified") and task_id not in announced)
        rows.append(row)
    rows.sort(key=lambda row: row.get("created_at") or 0, reverse=True)
    return rows
