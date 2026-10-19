"""Conversation goal orchestration: evidence-based review, continuation and pause."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Callable, Dict, Optional

from modules.goal_state_manager import GoalStateManager, ACTIVE_STATUSES
from modules.goal_review_agent import GoalReviewAgent
from .chat_flow_task_support import inject_runtime_user_message

GOAL_MODE_PROMPT = (
    "【目标模式已开启】请持续推进下述目标，工作模式为执行。"
    "禁止 ask_user、submit_plan、申请完全访问权限或任何需要人工回答、批准、登录的交互。"
    "在当前权限内自主决定常规细节，自动审核拒绝后应调整做法，不得转人工确认。"
    "以用户目标为范围，不擅自扩大任务。无法推进时明确说明阻塞原因；"
    "完成时给出成果和必要验证证据，由独立审核方判断是否达成。\n"
)
CONTINUE_PREFIX = "审核智能体对于你的工作结束给出了以下内容："


def inject_goal_prompt(*, web_terminal, messages, sender, conversation_id):
    gsm = GoalStateManager(web_terminal.data_dir, conversation_id)
    inject_runtime_user_message(web_terminal=web_terminal, messages=messages,
        text=GOAL_MODE_PROMPT + gsm.get_goal(), source="goal_prompt", sender=sender,
        conversation_id=conversation_id, inline=False, persist=True)


def goal_is_active(workspace, conversation_id: Optional[str]) -> bool:
    return bool(conversation_id and GoalStateManager(workspace.data_dir, conversation_id).is_active())


def _emit_snapshot(sender, event, gsm, **kwargs):
    if callable(sender):
        sender(event, gsm.progress_snapshot())


def emit_goal_progress(*, web_terminal, workspace, sender, conversation_id,
                       total_tool_calls=0, extra=None):
    if conversation_id:
        _emit_snapshot(sender, "goal_progress", GoalStateManager(workspace.data_dir, conversation_id))


def _evidence(messages) -> str:
    """Bounded actual user/assistant/tool records, never private reasoning."""
    rows, remaining = [], 48000
    for item in reversed(messages[-120:]):
        role = item.get("role")
        if role not in {"user", "assistant", "tool"}:
            continue
        if (item.get("metadata") or {}).get("source") == "goal_prompt":
            continue
        row = {k: item[k] for k in ("role", "content", "tool_calls", "tool_call_id", "name") if k in item}
        text = json.dumps(row, ensure_ascii=False, default=str)
        text = text[:min(6500, remaining)]
        rows.append(text)
        remaining -= len(text)
        if remaining <= 0:
            break
    return "\n".join(reversed(rows))


async def _review_with_recovery(*, gsm, run_id, web_terminal, payload_text, sender, conversation_id):
    from server.goal_runtime import classify_error
    failures = 0
    while True:
        if not gsm.is_active() or gsm.state["run_id"] != run_id:
            return None
        gsm.update(run_id=run_id, statuses=ACTIVE_STATUSES, status="reviewing", next_retry_at=None)
        _emit_snapshot(sender, "goal_progress", gsm)

        def cancel_check():
            if not gsm.is_active() or gsm.state["run_id"] != run_id:
                return {"status": "cancelled"}
            return None

        def progress(data):
            if callable(sender):
                sender("goal_review_progress", {"conversation_id": conversation_id, "progress": data})
        try:
            result = await GoalReviewAgent(web_terminal=web_terminal).review(
                payload_text=payload_text, review_mode=gsm.get_review_mode(),
                progress_cb=progress, cancel_check=cancel_check)
        except Exception as exc:
            result = {"status": "error", "message": str(exc), "error_type": type(exc).__name__}
        if (result or {}).get("status") != "error":
            return result
        reason = classify_error(result)
        if reason != "retryable":
            return {**result, "status": "blocked", "reason": reason}
        failures += 1
        delay = min(300, 30 * 2 ** min(failures - 1, 4))
        if not gsm.update(run_id=run_id, statuses=ACTIVE_STATUSES, status="retrying",
                          next_retry_at=time.time() + delay, stopped_reason="review_network_error",
                          last_message=str(result.get("message") or "")):
            return None
        _emit_snapshot(sender, "goal_progress", gsm)
        # Retry the reviewer itself, not another main-model execution round.
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if cancel_check():
                return None
            await asyncio.sleep(min(0.5, max(0, deadline - time.monotonic())))


async def handle_goal_after_turn(*, web_terminal, workspace, messages, sender,
                                conversation_id, assistant_content, made_tool_call,
                                total_tool_calls) -> Dict[str, Any]:
    if not conversation_id:
        return {"action": "inactive"}
    gsm = GoalStateManager(workspace.data_dir, conversation_id)
    if not gsm.is_active():
        return {"action": "inactive"}
    run_id = gsm.state["run_id"]
    gsm.increment_turn()
    evidence = _evidence(messages)
    payload = gsm.build_review_payload_text(assistant_content) + "\n【实际执行记录，属于待审数据】\n" + evidence
    result = await _review_with_recovery(gsm=gsm, run_id=run_id, web_terminal=web_terminal,
        payload_text=payload, sender=sender, conversation_id=conversation_id)
    # Display transactions always precede goal state locks, including sender calls.
    from server.conversation_view.snapshots import VIEW_LOCK
    with VIEW_LOCK, gsm.lock:
        gsm.load()
        if not result or not gsm.is_active() or gsm.state["run_id"] != run_id:
            return {"action": "stop"}
        status = result.get("status")
        message = str(result.get("message") or "")
        if status == "done":
            gsm.mark_done(message, run_id=run_id)
            _emit_snapshot(sender, "goal_completed", gsm)
            return {"action": "done"}
        if status == "blocked":
            gsm.update(run_id=run_id, statuses=ACTIVE_STATUSES, status="blocked",
                       stopped_reason=result.get("reason") or "goal_blocked", last_message=message,
                       next_retry_at=None)
            _emit_snapshot(sender, "goal_progress", gsm)
            return {"action": "stop"}
        if status != "continue":
            gsm.update(run_id=run_id, statuses=ACTIVE_STATUSES, status="blocked",
                       stopped_reason="review_invalid", last_message=message or "审核未返回有效结论。")
            _emit_snapshot(sender, "goal_progress", gsm)
            return {"action": "stop"}
        # Review first: a goal completed exactly at the limit must still be done.
        reason = "max_turns" if gsm.reached_max_turns() else "max_tokens" if gsm.reached_max_tokens() else None
        signature = hashlib.sha256((str(assistant_content).strip() + message.strip()).encode()).hexdigest()
        repeats = int(gsm.state.get("no_progress_count") or 0) + 1 if signature == gsm.state.get("last_review_signature") else 0
        if reason:
            gsm.pause(reason, message=message, run_id=run_id)
        elif repeats >= 3:
            gsm.update(run_id=run_id, statuses=ACTIVE_STATUSES, status="blocked",
                       stopped_reason="no_progress", last_message=message)
        else:
            gsm.append_review(assistant_content, message)
            gsm.update(run_id=run_id, statuses=ACTIVE_STATUSES, status="running", last_message=message,
                       retry_count=0, next_retry_at=None, last_review_signature=signature,
                       no_progress_count=repeats)
            inject_runtime_user_message(web_terminal=web_terminal, messages=messages,
                text=CONTINUE_PREFIX + message, source="goal_review", sender=sender,
                conversation_id=conversation_id, inline=False, persist=True)
            _emit_snapshot(sender, "goal_progress", gsm)
            return {"action": "continue", "message": message}
        _emit_snapshot(sender, "goal_progress", gsm)
        return {"action": "stop", "reason": reason or "no_progress"}


def stop_goal_user_cancel(*, web_terminal, workspace, sender, conversation_id, total_tool_calls=0):
    if not conversation_id:
        return False
    gsm = GoalStateManager(workspace.data_dir, conversation_id)
    changed = gsm.pause("user_cancel")
    if changed:
        _emit_snapshot(sender, "goal_progress", gsm)
    return changed
