"""Goal task admission, terminal outcomes and cancellable recovery scheduling.

No startup scanning or automatic restart recovery: only tasks accepted by this
process can schedule retries. Every retry is a new user input in the same chat.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from modules.goal_policy import validate_limits
from modules.goal_state_manager import ACTIVE_STATUSES, GoalStateManager

_TIMERS: dict[tuple, threading.Timer] = {}
_TIMER_LOCK = threading.Lock()


def manager_for_record(rec):
    from server.context.data import get_workspace_data
    from server.context import RuntimeIdentity
    p = rec.principal
    identity = RuntimeIdentity(host_mode=p.host_mode, host_workspace_id=p.host_workspace_id,
                               is_api_user=p.is_api_user, role=p.role)
    data = get_workspace_data(p.username, p.workspace_id, identity=identity)
    if data.manager_for(rec.conversation_id).read_conversation(rec.conversation_id) is None:
        raise ValueError("Conversation not found")
    return GoalStateManager(data.workspace.data_dir, rec.conversation_id)


def prepare_goal_task(rec) -> None:
    """Called after normal admission reserves the conversation, before thread start."""
    action = rec.task_params.goal_action
    if rec.task_type == "compression" or not rec.conversation_id:
        return
    gsm = manager_for_record(rec)
    if not action:
        with gsm.lock:
            gsm.load()
            if (rec.directives.auto_user_message_event
                    and gsm.state["status"] in {"paused", "blocked"}):
                raise RuntimeError("Goal is paused; automatic notifications remain pending")
            if gsm.is_active():
                gsm.update(statuses=ACTIVE_STATUSES, status="running", task_id=rec.task_id,
                           next_retry_at=None)
                rec.goal_run_id = gsm.state["run_id"]
                cancel_retry(rec.username, rec.workspace_id, rec.conversation_id)
        return
    gsm = manager_for_record(rec)
    limits = validate_limits(rec.task_params.goal_options)
    if action == "start":
        from modules.personalization_manager import load_personalization_config
        cfg = load_personalization_config(gsm.data_dir)
        state = gsm.start(goal=rec.message, review_mode=cfg.get("goal_review_mode", "readonly"),
                          task_id=rec.task_id, **(limits or {}))
    elif action == "continue":
        state = gsm.resume(task_id=rec.task_id, limits=limits)
    else:
        raise ValueError("Invalid goal action")
    rec.goal_run_id = state["run_id"]


def classify_error(error: Any) -> str:
    """Provider code/type first; message fallback for providers without codes."""
    data = error if isinstance(error, dict) else {"message": str(error)}
    code = " ".join(str(data.get(k) or "").lower() for k in ("error_code", "error_type", "code", "type"))
    text = code + " " + " ".join(str(data.get(k) or "").lower()
                                  for k in ("message", "error_message", "error_text"))
    quota = ("insufficient_quota", "usage_limit", "usage limit", "out_of_usage", "out of usage",
             "outof usage", "outofusage", "credit balance", "insufficient balance", "quota_exceeded",
             "quota exceeded", "billing_hard_limit", "额度不足", "额度已用", "余额不足", "用量上限")
    if any(word in text for word in quota):
        return "quota_exhausted"
    status = data.get("status_code")
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    if status in (401, 403) or any(word in text for word in ("authentication", "invalid_api_key", "unauthorized", "http 401", "http 403")):
        return "authentication"
    if status in (400, 404, 405, 413, 422) or any(word in text for word in ("invalid_request", "context_overflow", "no_model", "not configured", "profile incomplete")):
        return "configuration"
    if status == 429 or any(word in text for word in ("rate_limit", "rate limit", "http 429")):
        return "retryable"
    if status in (408, 409, 425) or (isinstance(status, int) and status >= 500):
        return "retryable"
    if any(word in text for word in ("timeout", "timed out", "connection", "connecterror", "readerror", "writeerror", "protocolerror", "network", "disconnect", "断流", "超时", "连接", "网络")):
        return "retryable"
    # Unknown failures must remain visible and manually resumable, not spin forever.
    return "unknown_error"


def emit_state(task_manager, rec, gsm):
    task_manager._append_event(rec, "goal_progress", gsm.progress_snapshot())


def finish_goal_task(task_manager, rec) -> None:
    """Run after gate release and terminal status; never revive cancelled work."""
    run_id = getattr(rec, "goal_run_id", None)
    if not run_id:
        return
    gsm = manager_for_record(rec)
    with gsm.lock:
        gsm.load()
        if gsm.state.get("run_id") != run_id or gsm.state.get("task_id") != rec.task_id:
            return
        if rec.stop_requested:
            gsm.pause("user_cancel", run_id=run_id)
        elif gsm.state["status"] in {"running", "reviewing"}:
            error = getattr(rec, "goal_error", None)
            reason = classify_error(error) if error else "execution_interrupted"
            if error and reason == "retryable":
                count = int(gsm.state.get("retry_count") or 0) + 1
                delay = min(300, 30 * (2 ** min(count - 1, 4)))
                requested = error.get("retry_after") if isinstance(error, dict) else None
                if isinstance(requested, (int, float)):
                    delay = max(delay, min(3600, max(0, requested)))
                gsm.update(run_id=run_id, statuses=ACTIVE_STATUSES, status="retrying",
                           retry_count=count, next_retry_at=time.time() + delay,
                           stopped_reason="network_error", last_message=_error_message(error))
            else:
                gsm.update(run_id=run_id, statuses=ACTIVE_STATUSES, status="blocked",
                           stopped_reason=reason, last_message=_error_message(error) or "执行已中断，点击继续重试。",
                           next_retry_at=None)
        retry = gsm.state["status"] == "retrying"
    emit_state(task_manager, rec, gsm)
    if retry:
        schedule_retry(task_manager, rec, gsm)


def _error_message(error):
    if isinstance(error, dict):
        return str(error.get("message") or error.get("error_message") or error.get("error_text") or "")[:3000]
    return str(error or "")[:3000]


def schedule_retry(task_manager, rec, gsm, *, delay_override=None):
    state = gsm.load()
    key = (rec.username, rec.workspace_id, rec.conversation_id)
    run_id, scheduled_task = state["run_id"], state.get("task_id")
    scheduled_at = state.get("next_retry_at")
    delay = max(0, float(scheduled_at or time.time()) - time.time())
    if delay_override is not None:
        delay = delay_override

    def wake():
        with _TIMER_LOCK:
            if _TIMERS.get(key) is not timer:
                return
            _TIMERS.pop(key, None)
        try:
            current = manager_for_record(rec)
        except Exception as exc:
            from server.utils_common import debug_log
            debug_log(f"[Goal] retry target unavailable: {exc}")
            return
        current.load()
        if (current.state["status"] != "retrying" or current.state["run_id"] != run_id
                or current.state.get("task_id") != scheduled_task
                or current.state.get("next_retry_at") != scheduled_at):
            return
        # VIEW_LOCK -> state lock -> task lock is the common admission order.
        from server.conversation_view.snapshots import VIEW_LOCK
        from server.runtime import RuntimeContext, TaskParams, InternalDirectives, runtime_service
        try:
            with VIEW_LOCK:
                with current.lock:
                    current.load()
                    if (current.state["status"] != "retrying" or current.state["run_id"] != run_id
                            or current.state.get("task_id") != scheduled_task
                            or current.state.get("next_retry_at") != scheduled_at):
                        return
                    active = runtime_service.list_runs(rec.username, rec.workspace_id,
                                                       conversation_id=rec.conversation_id, status="active")
                    if active:
                        schedule_retry(task_manager, rec, current, delay_override=2)
                        return
                    text = ("前次执行因暂时故障中断。请继续当前目标，从已保存的进度推进，"
                            "先核实已执行的操作，避免重复产生副作用。\n"
                            f"目标：{current.get_goal()}\n最近错误：{current.state.get('last_message', '')}")
                    ctx = RuntimeContext(principal=rec.principal,
                        params=TaskParams(message=text, conversation_id=rec.conversation_id,
                                          message_source="goal_recovery", task_type="notice"),
                        directives=InternalDirectives(auto_user_message_event=True,
                            auto_user_message_payload={"message_source": "goal_recovery", "visibility": "compact", "starts_work": True}))
                    runtime_service.create_task(ctx)
        except Exception as exc:
            from server.utils_common import debug_log
            debug_log(f"[Goal] retry dispatch failed: {exc}")
            current.load()
            if current.state["status"] == "retrying" and current.state["run_id"] == run_id:
                schedule_retry(task_manager, rec, current, delay_override=10)

    timer = threading.Timer(delay, wake)
    timer.daemon = True
    with _TIMER_LOCK:
        previous = _TIMERS.pop(key, None)
        if previous:
            previous.cancel()
        _TIMERS[key] = timer
    timer.start()


def cancel_retry(username, workspace_id, conversation_id):
    with _TIMER_LOCK:
        timer = _TIMERS.pop((username, workspace_id, conversation_id), None)
    if timer:
        timer.cancel()
