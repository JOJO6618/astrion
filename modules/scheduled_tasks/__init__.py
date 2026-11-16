"""User-scoped schedules, owned by the existing backend process."""
from pathlib import Path
from threading import Lock

_lock = Lock()
_service = None


def get_service():
    global _service
    with _lock:
        if _service is None:
            from config import DATA_DIR
            from .store import ScheduleStore
            from .service import SchedulerService
            from .runtime import AgentRuntimeAdapter
            _service = SchedulerService(ScheduleStore(Path(DATA_DIR) / 'scheduled_tasks.sqlite3'), AgentRuntimeAdapter())
        return _service
