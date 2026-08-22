"""目标模式（Goal Mode）在 Web 任务主循环中的编排逻辑。

目标状态是对话级的（见 modules/goal_state_manager.py）：每个对话独立持有
自己的目标，多对话并发互不影响。本模块将启动、提示词注入、turn 结束后的
审核/续命/停止判定集中在此，尽量减少对 chat_flow_task_main.py 的侵入。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from modules.goal_state_manager import (
    GoalStateManager,
    REASON_IDLE_NO_TOOL,
    REASON_MAX_TOKENS,
    REASON_MAX_TURNS,
    REASON_USER_CANCEL,
)
from modules.goal_review_agent import GoalReviewAgent
from modules.personalization_manager import load_personalization_config

from .chat_flow_task_support import inject_runtime_user_message

# ① 注入的目标模式提示词（开头一次 + 每次压缩后重注入）
GOAL_MODE_PROMPT = (
    "【目标模式已开启】以上是本次的目标。在目标达成前，请持续推进，"
    "不要把控制权交还给我、也不要停下来等我确认。请用实际的工具操作推进工作，"
    "避免只停留在计划或反复询问。每当你认为工作告一段落停下时，会有独立的审核方"
    "对照目标检查是否真正达成；若未达成，你会收到具体的下一步指示并继续。"
)

# 续命消息前缀
CONTINUE_PREFIX = "审核智能体对于你的工作结束给出了以下内容："


def _load_token_total(web_terminal) -> int:
    """读取工作区累计 total_tokens。"""
    try:
        totals = web_terminal.context_manager._load_token_totals()
        return int(totals.get("total_tokens") or 0)
    except Exception:
        return 0


def snapshot_token_baseline(web_terminal) -> Dict[str, int]:
    try:
        totals = web_terminal.context_manager._load_token_totals()
        return {
            "input_tokens": int(totals.get("input_tokens") or 0),
            "output_tokens": int(totals.get("output_tokens") or 0),
            "total_tokens": int(totals.get("total_tokens") or 0),
        }
    except Exception:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def maybe_start_goal(
    *,
    web_terminal,
    workspace,
    conversation_id: str,
    goal_text: str,
    current_tool_calls: int,
) -> bool:
    """为本对话启动一个新目标。

    目标状态是对话级的：启动会覆盖本对话自己的旧目标状态（running/done/stopped、
    review_history、token 基线、开始时间等），不影响其他对话的目标。
    """
    if not conversation_id:
        return False
    gsm = GoalStateManager(workspace.data_dir, conversation_id)
    cfg = load_personalization_config(workspace.data_dir)
    review_mode = cfg.get("goal_review_mode") or "readonly"
    end_conditions = cfg.get("goal_end_conditions") or ["max_turns"]
    max_turns = cfg.get("goal_max_turns") if "max_turns" in end_conditions else None
    max_tokens = cfg.get("goal_max_tokens") if "max_tokens" in end_conditions else None
    gsm.start(
        goal=goal_text,
        review_mode=review_mode,
        max_turns=max_turns,
        max_tokens=max_tokens,
        token_baseline=snapshot_token_baseline(web_terminal),
        tool_call_baseline=current_tool_calls,
    )
    return True


def inject_goal_prompt(
    *,
    web_terminal,
    messages,
    sender: Optional[Callable[[str, Dict[str, Any]], None]],
    conversation_id: Optional[str],
) -> None:
    """注入 ① 号目标模式提示词（开头 / 压缩后重注入共用）。"""
    inject_runtime_user_message(
        web_terminal=web_terminal,
        messages=messages,
        text=GOAL_MODE_PROMPT,
        source="goal_prompt",
        sender=sender,
        conversation_id=conversation_id,
        inline=False,
        persist=True,
    )


def goal_is_active(workspace, conversation_id: Optional[str]) -> bool:
    if not conversation_id:
        return False
    try:
        return GoalStateManager(workspace.data_dir, conversation_id).is_active()
    except ValueError:
        return False


def _emit_snapshot(sender, event: str, gsm: GoalStateManager, *, total_tokens: int, tool_calls: int, extra: Optional[Dict] = None):
    if not callable(sender):
        return
    snap = gsm.progress_snapshot(current_total_tokens=total_tokens, current_tool_calls=tool_calls)
    if extra:
        snap.update(extra)
    try:
        sender(event, snap)
    except Exception:
        pass


def emit_goal_progress(
    *,
    web_terminal,
    workspace,
    sender: Optional[Callable[[str, Dict[str, Any]], None]],
    conversation_id: Optional[str],
    total_tool_calls: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """广播本对话的目标模式进度（快照自带 conversation_id，前端按对话过滤），
    用于刷新后恢复与进度弹窗动态数字。"""
    if not conversation_id:
        return
    try:
        gsm = GoalStateManager(workspace.data_dir, conversation_id)
    except ValueError:
        return
    if not gsm.state:
        return
    total_tokens = _load_token_total(web_terminal)
    _emit_snapshot(
        sender,
        "goal_progress",
        gsm,
        total_tokens=total_tokens,
        tool_calls=total_tool_calls,
        extra=extra,
    )


async def handle_goal_after_turn(
    *,
    web_terminal,
    workspace,
    messages,
    sender: Optional[Callable[[str, Dict[str, Any]], None]],
    conversation_id: Optional[str],
    assistant_content: str,
    made_tool_call: bool,
    total_tool_calls: int,
) -> Dict[str, Any]:
    """主模型某轮结束（无 tool_calls）后的目标处理。

    返回 {'action': 'inactive'|'stop'|'done'|'continue', ...}：
    - inactive：本对话无活动目标，调用方照常结束本次任务。
    - stop：目标因空转/边界停止，调用方照常结束（事件已发）。
    - done：目标达成，调用方照常结束（事件已发）。
    - continue：已注入续命消息，调用方应 continue 回主循环开下一轮。
    """
    if not conversation_id:
        return {"action": "inactive"}
    try:
        gsm = GoalStateManager(workspace.data_dir, conversation_id)
    except ValueError:
        return {"action": "inactive"}
    if not gsm.is_active():
        return {"action": "inactive"}

    total_tokens = _load_token_total(web_terminal)
    _emit_snapshot(sender, "goal_progress", gsm, total_tokens=total_tokens, tool_calls=total_tool_calls)

    # 1) 空转保护：本目标轮主模型一次工具都没调过 → 直接停止
    if not made_tool_call:
        gsm.mark_stopped(REASON_IDLE_NO_TOOL)
        _emit_snapshot(sender, "goal_stopped", gsm, total_tokens=total_tokens, tool_calls=total_tool_calls)
        return {"action": "stop", "reason": REASON_IDLE_NO_TOOL}

    # 2) 计入一轮，检查结束边界
    gsm.increment_turn()
    _emit_snapshot(sender, "goal_progress", gsm, total_tokens=total_tokens, tool_calls=total_tool_calls)
    if gsm.reached_max_turns():
        gsm.mark_stopped(REASON_MAX_TURNS)
        _emit_snapshot(sender, "goal_stopped", gsm, total_tokens=total_tokens, tool_calls=total_tool_calls)
        return {"action": "stop", "reason": REASON_MAX_TURNS}
    if gsm.reached_max_tokens(total_tokens):
        gsm.mark_stopped(REASON_MAX_TOKENS)
        _emit_snapshot(sender, "goal_stopped", gsm, total_tokens=total_tokens, tool_calls=total_tool_calls)
        return {"action": "stop", "reason": REASON_MAX_TOKENS}

    # 3) 调用审核智能体
    payload_text = gsm.build_review_payload_text(assistant_content)
    if callable(sender):
        try:
            sender("goal_review_progress", {"conversation_id": conversation_id, "progress": {"stage": "start", "message": "开始审核"}})
        except Exception:
            pass
    try:
        agent = GoalReviewAgent(web_terminal=web_terminal)
        def _progress(progress: Dict[str, Any]) -> None:
            if callable(sender):
                try:
                    sender("goal_review_progress", {"conversation_id": conversation_id, "progress": progress})
                except Exception:
                    pass

        result = await agent.review(
            payload_text=payload_text,
            review_mode=gsm.get_review_mode(),
            progress_cb=_progress,
        )
    except Exception as exc:
        # 审核异常 → 保守续命，但仍受边界约束
        result = {"status": "continue", "message": f"目标审核出现异常（{exc}），请继续核对目标并推进未完成的部分。"}

    status = (result or {}).get("status")
    message = (result or {}).get("message") or ""

    if status == "done":
        gsm.mark_done(message)
        _emit_snapshot(
            sender, "goal_completed", gsm,
            total_tokens=total_tokens, tool_calls=total_tool_calls,
            extra={"summary": message},
        )
        return {"action": "done"}

    # continue：记录审核历史 + 注入续命消息
    gsm.append_review(assistant_content, message)
    inject_runtime_user_message(
        web_terminal=web_terminal,
        messages=messages,
        text=f"{CONTINUE_PREFIX}{message}",
        source="goal_review",
        sender=sender,
        conversation_id=conversation_id,
        inline=False,
        persist=True,
    )
    return {"action": "continue", "message": message}


def stop_goal_user_cancel(*, web_terminal, workspace, sender, conversation_id: Optional[str], total_tool_calls: int = 0) -> bool:
    """用户停止任务时，若本对话有活动目标则一并停止。返回是否有活动目标被停止。"""
    if not conversation_id:
        return False
    try:
        gsm = GoalStateManager(workspace.data_dir, conversation_id)
    except ValueError:
        return False
    if not gsm.is_active():
        return False
    total_tokens = _load_token_total(web_terminal)
    gsm.mark_stopped(REASON_USER_CANCEL)
    _emit_snapshot(sender, "goal_stopped", gsm, total_tokens=total_tokens, tool_calls=total_tool_calls)
    return True
