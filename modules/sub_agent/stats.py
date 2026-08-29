"""子智能体统计摘要与消息构建工具。"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from modules.i18n import tr


class SubAgentStatsMixin:
    """提供子智能体任务耗时、统计摘要与结果消息组装能力。"""

    @staticmethod
    def _compute_elapsed_seconds(task: Dict) -> Optional[int]:
        try:
            created_at = float(task.get("created_at") or 0)
            updated_at = float(task.get("updated_at") or time.time())
        except (TypeError, ValueError):
            return None
        if created_at <= 0:
            return None
        return int(round(max(0.0, updated_at - created_at)))

    @staticmethod
    def _coerce_stat_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _build_stats_summary(self, stats: Optional[Dict[str, Any]]) -> str:
        if not isinstance(stats, dict):
            stats = {}
        api_calls = self._coerce_stat_int(stats.get("api_calls") or stats.get("turn_count"))
        files_read = self._coerce_stat_int(stats.get("files_read"))
        write_files = self._coerce_stat_int(stats.get("write_files"))
        edit_files = self._coerce_stat_int(stats.get("edit_files"))
        searches = self._coerce_stat_int(stats.get("searches"))
        web_pages = self._coerce_stat_int(stats.get("web_pages"))
        commands = self._coerce_stat_int(stats.get("commands"))
        lines = [
            tr("sub_agent_stats.api_calls", n=api_calls),
            tr("sub_agent_stats.files_read", n=files_read),
            tr("sub_agent_stats.write_files", n=write_files),
            tr("sub_agent_stats.edit_files", n=edit_files),
            tr("sub_agent_stats.searches", n=searches),
            tr("sub_agent_stats.web_pages", n=web_pages),
            tr("sub_agent_stats.commands", n=commands),
        ]
        return "\n".join(lines)

    def _compose_sub_agent_message(
        self,
        *,
        prefix: str,
        stats_summary: str,
        summary: str,
        deliverables_dir: Optional[str] = None,
        duration_seconds: Optional[int] = None,
    ) -> str:
        parts = [prefix]
        if stats_summary:
            parts.append(stats_summary)
        if duration_seconds is not None:
            parts.append(tr("sub_agent_stats.duration", n=duration_seconds))
        if summary:
            parts.append(summary)
        if deliverables_dir:
            parts.append(tr("sub_agent_stats.deliverables", dir=deliverables_dir))
        return "\n\n".join(parts)
