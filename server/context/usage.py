"""配额追踪器（UsageTracker）的获取与广播。"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import modules.user_manager

from modules.usage_tracker import UsageTracker
from server import state


def get_or_create_usage_tracker(username: Optional[str], workspace: Optional['modules.user_manager.UserWorkspace'] = None) -> Optional[UsageTracker]:
    if not username:
        return None
    tracker = state.usage_trackers.get(username)
    if tracker:
        return tracker
    from modules.user_manager import UserWorkspace  # noqa: F401  # 保持类型引用兼容
    if workspace is None:
        workspace = state.user_manager.ensure_user_workspace(username)
    record = state.user_manager.get_user(username)
    role = getattr(record, "role", "user") if record else "user"
    tracker = UsageTracker(str(workspace.data_dir), role=role or "user")
    state.usage_trackers[username] = tracker
    return tracker


def emit_user_quota_update(username: Optional[str]):
    from server.extensions import emit_event
    if not username:
        return
    tracker = get_or_create_usage_tracker(username)
    if not tracker:
        return
    try:
        snapshot = tracker.get_quota_snapshot()
        emit_event('quota_update', {'quotas': snapshot}, room=f"user_{username}")
    except Exception:
        pass
