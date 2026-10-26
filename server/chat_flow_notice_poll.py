from __future__ import annotations
import asyncio
import time
from typing import Any, Dict, List
from modules.multi_agent.debug_logger import ma_debug
from .utils_common import debug_log
from .main_task_gate import try_acquire_main_task_gate, release_main_task_gate
from .state import get_stop_flag
from .chat_flow_notice_state import _collect_pending_completion_notices
from .chat_flow_notice_dispatch import _dispatch_completion_user_notice
from .chat_flow_notice_idle import _dispatch_multi_agent_idle_messages_sync
from .chat_flow_notice_state import _has_pending_completion_work
from .chat_flow_notice_state import _has_pending_workflow_notices
from .chat_flow_notice_state import _rollback_completion_notice_marks


async def poll_completion_notifications(*, web_terminal, workspace, conversation_id, client_sid, username):
    """统一的“通知池”轮询器。

    把子智能体完成通知与后台 run_command 完成通知合并到同一条链路：
    - 每轮把两路所有待通知项一次性取出（池化），按时间排序；
    - 这一批里前 N-1 条作为「预写通知」随同一个后续任务一起注入（写历史 + 事件流），
      不各自触发新一轮工作；
    - 仅最后 1 条作为后续 chat task 的触发消息，启动一次「工作 → 停止」循环；
    - 该后续任务结束后回到 handle_task_with_sender 结尾重新 spawn 本轮询器，继续消费剩余通知。

    这样多个后台任务同时完成时，会被合并成一次（而非每条都触发一轮停止-再工作）。
    """

    sub_manager = getattr(web_terminal, "sub_agent_manager", None)
    bg_manager = getattr(web_terminal, "background_command_manager", None)
    if not sub_manager and not bg_manager:
        return
    if not hasattr(web_terminal, "_announced_sub_agent_tasks"):
        web_terminal._announced_sub_agent_tasks = set()

    max_wait_time = 3600  # 最多等待 1 小时
    start_wait = time.time()

    def sender(event_type, data):
        # WebSocket 推送已移除：事件均经任务事件流（_append_event）由轮询消费；
        # 此 sender 仅保留签名以兼容既有调用点，不再做任何实时推送。
        return

    loop_count = 0
    ma_debug("poll_completion_notifications_start", conversation_id=conversation_id)
    while (time.time() - start_wait) < max_wait_time:
        loop_count += 1
        from modules.goal_policy import goal_blocks_background
        if goal_blocks_background(web_terminal.data_dir, conversation_id):
            await asyncio.sleep(1)
            continue
        # 检查停止标志
        client_stop_info = get_stop_flag(client_sid, username, include_user=False)
        if client_stop_info:
            stop_requested = client_stop_info.get('stop', False) if isinstance(client_stop_info, dict) else client_stop_info
            if stop_requested:
                ma_debug("poll_completion_notifications_stop_requested", conversation_id=conversation_id)
                break

        # 若主对话仍在工具循环中，暂不消费完成事件，避免抢占 system 消息插入
        if getattr(web_terminal, "_tool_loop_active", False):
            ma_debug("poll_completion_notifications_wait_tool_loop", conversation_id=conversation_id)
            await asyncio.sleep(1)
            continue

        # 多智能体模式：主对话任务仍在运行时，由主循环自己消费 pending 消息
        if getattr(web_terminal, "_multi_agent_main_task_active", False):
            ma_debug("poll_completion_notifications_wait_main_task", conversation_id=conversation_id)
            await asyncio.sleep(1)
            continue

        # 主任务门闸：仅当对话没有正在运行的主任务时才预占门闸。
        # 防止通知任务与主任务并发交叉写入对话历史（2026-08-12 平行时空事故）。
        # 预占成功后排发：门闸 token 经 RuntimeContext.directives 移交给新任务线程认领释放；
        # 排发失败则释放门闸并回滚通知标记，下轮重新收集。
        gate_token = try_acquire_main_task_gate(web_terminal)
        if gate_token is None:
            ma_debug("poll_completion_notifications_wait_main_task_gate", conversation_id=conversation_id)
            await asyncio.sleep(1)
            continue

        ma_debug("poll_completion_notifications_collect", conversation_id=conversation_id, loop_count=loop_count)
        notices = _collect_pending_completion_notices(
            web_terminal=web_terminal,
            conversation_id=conversation_id,
        )
        ma_debug("poll_completion_notifications_collected", conversation_id=conversation_id, notice_count=len(notices))

        if not notices:
            # 本轮没有待通知项：释放门闸后按原逻辑判断退出或继续
            release_main_task_gate(web_terminal, gate_token)

        if notices:
            # 取出后剩余是否还有未完成/未通知的后台工作（决定前端是否保持等待态）
            has_remaining = _has_pending_completion_work(
                web_terminal=web_terminal,
                conversation_id=conversation_id,
            )

            preceding = notices[:-1]
            last_notice = notices[-1]

            # 前 N-1 条预写通知：随后续任务注入历史/事件流，不单独触发工作
            preceding_payload: List[Dict[str, Any]] = []
            for item in preceding:
                payload = dict(item.get("payload") or {})
                payload["starts_work"] = False  # 预写通知渲染为完成态气泡，不起独立计时器
                preceding_payload.append({
                    "message": item.get("message") or "",
                    "payload": payload,
                })

            last_payload = dict(last_notice.get("payload") or {})
            last_payload.update({
                "has_running_sub_agents": has_remaining,
                "has_running_background_commands": has_remaining,
                "remaining_count": 1 if has_remaining else 0,
            })

            try:
                await _dispatch_completion_user_notice(
                    web_terminal=web_terminal,
                    workspace=workspace,
                    sender=sender,
                    client_sid=client_sid,
                    username=username,
                    conversation_id=conversation_id,
                    user_message=last_notice.get("message") or "",
                    extra_payload=last_payload,
                    preceding_notices=preceding_payload,
                    main_task_gate_token=gate_token,
                )
            except Exception as exc:
                # 派发失败：释放门闸 + 回滚通知标记，下轮轮询重新收集，杜绝静默丢失
                debug_log(f"[CompletionPoll] 派发完成通知失败，释放门闸并回滚标记: {exc}")
                release_main_task_gate(web_terminal, gate_token)
                _rollback_completion_notice_marks(web_terminal=web_terminal, notices=notices)
                ma_debug("poll_completion_notifications_dispatch_failed", conversation_id=conversation_id, error=str(exc))
                await asyncio.sleep(5)
                continue

            # 本轮已派发一个后续任务，门闸随任务移交（由任务线程 finally 释放），
            # 由该任务结束后重新 spawn 本轮询器继续消费剩余通知
            return

        # 没有待通知项：若也没有运行中的后台工作或工作流待通知，结束轮询
        pending = _has_pending_completion_work(
            web_terminal=web_terminal, conversation_id=conversation_id
        ) or _has_pending_workflow_notices(
            web_terminal=web_terminal, conversation_id=conversation_id
        )
        if not pending:
            break

        await asyncio.sleep(5)

async def poll_multi_agent_notifications(*, web_terminal, workspace, conversation_id, client_sid, username):
    """多智能体模式专用通知池。

    只在主任务结束后启动，负责两件事：
    1. 若主任务已空闲且 MultiAgentState 中有 pending_master_messages，
       一次性取出并触发新一轮主任务工作——即使仍有 running 子智能体也立即派发
       （主对话空闲时消息池有消息就该全部插入，设计中属情况2）。
    2. 若 pending 为空且仍有 running 实例，循环等待下一次 push 新输出。

    重要：旧实现要求所有 running 实例退出再 drain，导致 ask_master 阻塞中的
    子智能体永远等不到主对话回答而超时——现已修复为池优先。

    与 poll_completion_notifications 完全分离，避免多智能体消息和传统后台
    通知竞争 task_manager 的单工作区互斥。
    """

    def sender(event_type, data):
        # WebSocket 推送已移除：事件均经任务事件流（_append_event）由轮询消费。
        return

    import threading as _threading
    ma_debug(
        "poll_ma_notifications_start",
        conversation_id=conversation_id,
        thread_id=_threading.get_ident(),
        main_task_active_at_start=getattr(web_terminal, "_multi_agent_main_task_active", False),
    )

    tick_count = 0
    manager = getattr(web_terminal, "sub_agent_manager", None)
    while True:
        tick_count += 1
        from modules.goal_policy import goal_blocks_background
        if goal_blocks_background(web_terminal.data_dir, conversation_id):
            await asyncio.sleep(1)
            continue
        # 检查停止标志
        client_stop_info = get_stop_flag(client_sid, username, include_user=False)
        if client_stop_info:
            stop_requested = client_stop_info.get('stop', False) if isinstance(client_stop_info, dict) else client_stop_info
            if stop_requested:
                ma_debug("poll_ma_notifications_stop_requested", conversation_id=conversation_id, tick=tick_count)
                break

        manager = getattr(web_terminal, "sub_agent_manager", None)
        if not manager:
            ma_debug("poll_ma_no_manager", conversation_id=conversation_id, tick=tick_count)
            break

        state = manager.get_multi_agent_state(conversation_id)
        if not state:
            ma_debug("poll_ma_no_state", conversation_id=conversation_id, tick=tick_count)
            break

        # 主任务又被唤醒了（用户发送新消息等），让主循环自己处理，本通知池退出
        if getattr(web_terminal, "_multi_agent_main_task_active", False):
            ma_debug("poll_ma_notifications_main_task_active", conversation_id=conversation_id, tick=tick_count)
            break

        # 状态快照
        all_insts = state.list_all()
        statuses = [a.status for a in all_insts]
        pending_count_now = len(state.pending_master_messages)
        main_active = getattr(web_terminal, "_multi_agent_main_task_active", False)
        ma_debug(
            "poll_ma_tick",
            conversation_id=conversation_id,
            tick=tick_count,
            instance_count=len(all_insts),
            statuses=statuses,
            pending_count=pending_count_now,
            main_active=main_active,
        )

        # 1) 先检查 pending 池——主对话空闲时只要池里有消息就立即派发（情况2）。
        if state.has_pending_master_messages():
            gate_token = try_acquire_main_task_gate(web_terminal)
            if gate_token is None:
                await asyncio.sleep(0.5)
                continue
            from server.conversation_view.snapshots import VIEW_LOCK
            # No await inside this transaction: goal pause/close cannot interleave
            # between draining the notification pool and admitting its task.
            with VIEW_LOCK:
                if goal_blocks_background(web_terminal.data_dir, conversation_id):
                    release_main_task_gate(web_terminal, gate_token)
                    continue
                pending = state.drain_master_messages()
                try:
                    _dispatch_multi_agent_idle_messages_sync(
                        web_terminal=web_terminal, workspace=workspace, sender=sender,
                        client_sid=client_sid, username=username,
                        conversation_id=conversation_id, messages=pending, gate_token=gate_token,
                    )
                    ma_debug("poll_ma_dispatch_success", conversation_id=conversation_id, count=len(pending), tick=tick_count)
                except Exception as exc:
                    release_main_task_gate(web_terminal, gate_token)
                    state.restore_master_messages(pending)
                    manager._save_state()
                    debug_log(f"[MultiAgent] 分发 idle 消息失败，消息已回队: {exc}")
                    ma_debug("poll_ma_notifications_dispatch_error", conversation_id=conversation_id,
                             error=str(exc), error_type=type(exc).__name__, tick=tick_count)
                    pending = None
                else:
                    manager._save_state()
            if pending is not None:
                return
            await asyncio.sleep(0.5)
            continue

        # 2) 没有 pending 消息：检查还有哪些子智能体在运行，决定退出还是继续等
        running_agents = [a for a in all_insts if a.status == "running"]
        if not running_agents:
            ma_debug("poll_ma_notifications_no_work_end", conversation_id=conversation_id, tick=tick_count)
            break

        ma_debug(
            "poll_ma_notifications_wait_running",
            conversation_id=conversation_id,
            running_count=len(running_agents),
            tick=tick_count,
        )
        await asyncio.sleep(0.5)

    ma_debug("poll_ma_notifications_end", conversation_id=conversation_id, tick=tick_count)

    # poll 退出时，如果主智能体也空闲且没有 pending 消息，发送 task_complete 通知前端对话变空闲。
    # 否则前端会一直保持 taskInProgress=true，MultiAgentTaskProbe 会持续轮询但找不到任务。
    if not getattr(web_terminal, "_multi_agent_main_task_active", False):
        state = manager.get_multi_agent_state(conversation_id) if manager else None
        still_running = state and any(a.status == "running" for a in state.list_all())
        still_pending = state and state.has_pending_master_messages()
        if not still_running and not still_pending:
            sender('task_complete', {
                'total_iterations': 0,
                'total_tool_calls': 0,
                'auto_fix_attempts': 0,
                'has_running_sub_agents': False,
                'has_running_background_commands': False,
                'has_running_multi_agent': False,
                'pending_runtime_guidance_messages': [],
                'multi_agent_idle_signal': True,
            })
            ma_debug("poll_ma_idle_signal_sent", conversation_id=conversation_id, tick=tick_count)
