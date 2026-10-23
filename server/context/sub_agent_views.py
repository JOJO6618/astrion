"""Read the current child runtime format without creating execution resources."""
from __future__ import annotations

import json
from pathlib import Path

from modules.sub_agent.named import runtime_agent_view
from modules.sub_agent.stats import SubAgentStatsMixin
from server.context.background import persisted_sub_agents


def stored_overview(data_context, conversation_id):
    if not conversation_id:
        return []
    formatter = SubAgentStatsMixin()
    rows = []
    for task in (persisted_sub_agents(data_context).get("tasks") or {}).values():
        if task.get("conversation_id") != conversation_id:
            continue
        row = runtime_agent_view(task)
        row.update(display_name=task["name"], last_tool=task.get("last_tool"), current_context_tokens=0)
        try:
            stats = json.loads(Path(task["stats_file"]).read_text(encoding="utf-8"))
            row["current_context_tokens"] = stats.get("current_context_tokens", 0)
            row["stats_summary"] = formatter._build_stats_summary(stats)
        except (OSError, ValueError):
            pass
        rows.append(row)
    return sorted(rows, key=lambda row: row.get("created_at") or 0, reverse=True)
