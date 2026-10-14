"""Thin host-only bridge to CrossPet; never creates execution resources."""
from __future__ import annotations

import weakref

from config import IS_HOST_MODE
from modules.crosspet.observer import CrossPetObserver
from modules.crosspet.quota import SubscriptionQuota
from modules.crosspet.settings import control_path
from modules.crosspet.state import EVENTS


def _model_id(key: str) -> str:
    if not key:
        return ""
    from config.model_profiles import get_registered_model_profiles
    profile = get_registered_model_profiles().get(key) or {}
    return str((profile.get("fast") or {}).get("model_id") or key)


OBSERVER = CrossPetObserver(model_resolver=_model_id,
                            quota_source=SubscriptionQuota(control_path()) if IS_HOST_MODE else None)


def _identity(rec) -> tuple[str, str, str]:
    return str(rec.username), str(rec.workspace_id), str(rec.conversation_id or rec.task_id)


def task_event(rec, event_type: str, data: dict) -> None:
    if IS_HOST_MODE and event_type in EVENTS:
        OBSERVER.event(_identity(rec), rec.task_id, str(rec.model_key or ""), event_type, data or {})


def bind_task(manager, rec, terminal=None) -> None:
    if not IS_HOST_MODE:
        return
    ref = weakref.ref(terminal) if terminal is not None else lambda: None
    model = str(getattr(terminal, "model_key", None) or rec.model_key or "")

    def probe():
        with manager._lock:
            status = rec.status
        current = ref()
        background = None
        if current is not None:
            background = any(manager.get_conversation_running_status(current, rec.conversation_id).values())
        return status, background

    OBSERVER.bind(_identity(rec), rec.task_id, model, probe)
    OBSERVER.event(_identity(rec), rec.task_id, model, "crosspet_start", {})


def finish_task(rec) -> None:
    task_event(rec, "crosspet_finish", {"status": rec.status})


def focus_model(username: str, workspace: str, conversation: str, model: str) -> None:
    if IS_HOST_MODE:
        OBSERVER.focus((username, workspace, conversation), model)


def focus_tab(username: str, tab: dict | None) -> None:
    if not IS_HOST_MODE:
        return
    if not tab or tab.get("kind") != "conv" or not tab.get("workspace_id"):
        OBSERVER.focus(None)
        return
    workspace = str(tab["workspace_id"])
    cid = str(tab.get("conversation_id") or "")

    def load_model():
        from server.context.data import get_workspace_data
        data = get_workspace_data(username, workspace)
        saved = data.manager_for(cid).read_conversation(cid)
        return str(((saved or {}).get("metadata") or {}).get("model_key") or "")

    OBSERVER.focus((username, workspace, cid), load_model)
