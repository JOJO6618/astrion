"""Account quotas use personal storage without creating an execution workspace."""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import modules.user_manager

from modules.account_usage import account_tracker
from modules.usage_tracker import UsageTracker
from server import state


def get_or_create_usage_tracker(username: Optional[str], workspace: Optional['modules.user_manager.UserWorkspace'] = None) -> Optional[UsageTracker]:
    if not username:
        return None
    record = state.user_manager.get_user(username)
    if record is None:
        return None
    personal = state.user_manager._personal_root(record.username)
    tracker = account_tracker(personal, getattr(record, "role", "user") or "user")
    state.usage_trackers[username] = tracker
    return tracker
