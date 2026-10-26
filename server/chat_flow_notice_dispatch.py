from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from server.runtime import InternalDirectives, RuntimeContext, TaskParams, runtime_service
from modules.multi_agent.debug_logger import ma_debug
from .utils_common import debug_log
from .main_task_gate import release_main_task_gate
from .chat_flow_message_policy import _VALID_USER_MESSAGE_SOURCES
from .chat_flow_message_policy import _user_message_ui_defaults


def _persist_and_echo_preceding_notice(
    *,
    web_terminal,
    sender,
    conversation_id: str,
    message: str,
    payload: Dict[str, Any],
) -> None:
    """预写一条「前置完成通知」：写入对话历史 + 任务事件流回显（不触发新一轮工作）。

    用于「通知池」一次取出多条时，前 N-1 条随同一后续任务一起呈现。
    这些通知必须落历史，否则模型看不到、刷新后也会丢失。
    """
    message = (message or "").strip()
    if not message:
        return
    src = str(payload.get("message_source") or "sub_agent").strip().lower()
    if src not in _VALID_USER_MESSAGE_SOURCES:
        src = "sub_agent"
    ui_defaults = _user_message_ui_defaults(src, auto_user_message_event=True)
    # 前置通知渲染为完成态气泡：不开启独立工作计时器
    ui_defaults = {**ui_defaults, "starts_work": False}
    metadata = {
        "message_source": src,
        "is_auto_generated": True,
        "auto_message_type": "completion_notice",
        **ui_defaults,
    }
    import uuid
    payload["message_id"] = str(uuid.uuid4())
    try:
        cm = getattr(web_terminal, "context_manager", None)
        if cm is not None:
            saved = cm.add_conversation(
                "user", message, metadata=metadata, message_id=payload["message_id"]
            )
            payload["message_id"] = saved["message_id"]
    except Exception as exc:
        debug_log(f"[CompletionNotice] 前置通知写入历史失败: {exc}")
    # 回显经 sender 通道（任务内=任务事件流；poll 轮询器=no-op）；
    # 轮询客户端统一由后续任务事件流回放，前端按 message_id 更新同一消息。
    try:
        echo_payload = {
            "message": message,
            "conversation_id": conversation_id,
            "message_source": src,
            **ui_defaults,
            **{k: v for k, v in (payload or {}).items() if k not in {"message", "conversation_id"}},
        }
        echo_payload["starts_work"] = False
        echo_payload["metadata"] = {**metadata}
        sender("user_message", echo_payload)
    except Exception:
        pass

async def _dispatch_completion_user_notice(
    *,
    web_terminal,
    workspace,
    sender,
    client_sid: str,
    username: str,
    conversation_id: str,
    user_message: str,
    extra_payload: Optional[Dict[str, Any]] = None,
    preceding_notices: Optional[List[Dict[str, Any]]] = None,
    main_task_gate_token: Optional[str] = None,
):
    """复用子智能体完成后的 user 代发机制。

    preceding_notices：本批「通知池」里除最后一条以外的前置通知列表，
    每项为 {"message": str, "payload": dict}。这些会先于触发消息写入历史/事件流，
    但不各自触发新一轮工作；仅最后一条 user_message 作为后续任务的触发消息。
    """
    from modules.goal_policy import goal_blocks_background
    if goal_blocks_background(web_terminal.data_dir, conversation_id):
        raise RuntimeError("Goal is paused; completion notifications remain pending")
    extra_payload = extra_payload or {}
    preceding_notices = preceding_notices or []
    message_source = str(extra_payload.get("message_source") or "").strip().lower()
    if not message_source:
        if extra_payload.get("runtime_mode_notice"):
            message_source = "notify"
        elif extra_payload.get("runtime_guidance"):
            message_source = "guidance"
        elif extra_payload.get("background_command_notice"):
            message_source = "background_command"
        elif extra_payload.get("sub_agent_notice"):
            message_source = "sub_agent"
    if message_source not in _VALID_USER_MESSAGE_SOURCES:
        message_source = "user"

    # 先把前置通知写入历史并经 sender 回显（Socket.IO 移除后无即时推送，
    # 统一通过后续任务事件流回放，见 _run_chat_task 的 preceding_user_notices 注入）。
    for item in preceding_notices:
        _persist_and_echo_preceding_notice(
            web_terminal=web_terminal,
            sender=sender,
            conversation_id=conversation_id,
            message=str(item.get("message") or ""),
            payload=item.setdefault("payload", {}),
        )

    try:
        ui_defaults = _user_message_ui_defaults(
            message_source,
            auto_user_message_event=True,
        )
        # 关键：通知类后台任务需要把 user_message 写入任务事件流，
        # 否则前端轮询只会看到 AI/tool 事件，看不到 user_message。
        auto_payload = {
            **dict(extra_payload or {}),
            **ui_defaults,
            "timestamp": datetime.now().isoformat(),
        }
        # 轮询客户端通过后续任务事件流回放前置通知。
        preceding_list = None
        if preceding_notices:
            preceding_list = [
                {
                    "message": str(item.get("message") or ""),
                    "payload": dict(item.get("payload") or {}),
                }
                for item in preceding_notices
                if str(item.get("message") or "").strip()
            ]
        # 公共任务入口：内部调用方，从 terminal 构造可信上下文；
        # 模型/模式覆盖取 terminal 当前值（与偏好快照同值，对齐既有通知链语义）。
        ctx = RuntimeContext.from_terminal(
            web_terminal,
            workspace,
            username,
            params=TaskParams(
                message=user_message,
                conversation_id=conversation_id,
                message_source=message_source,
                model_key=getattr(web_terminal, "model_key", None),
                run_mode=getattr(web_terminal, "run_mode", None),
                thinking_mode=getattr(web_terminal, "thinking_mode", None),
            ),
            directives=InternalDirectives(
                # 派发方预占的主任务门闸 token 随任务移交，由任务线程认领
                main_task_gate_token=main_task_gate_token,
                auto_user_message_event=True,
                auto_user_message_payload=auto_payload,
                preceding_user_notices=preceding_list,
            ),
        )
        ma_debug(
            "dispatch_completion_create_chat_task",
            conversation_id=conversation_id,
            user_message_preview=user_message[:300],
            preceding_count=len(preceding_notices),
        )
        rec = runtime_service.create_task(ctx)
        ma_debug(
            "dispatch_completion_task_created",
            conversation_id=conversation_id,
            task_id=getattr(rec, "task_id", None),
        )
        payload = {
            'message': user_message,
            'conversation_id': conversation_id,
            'task_id': rec.task_id,
            'message_source': message_source,
            'timestamp': datetime.now().isoformat(),
            **ui_defaults,
        }
        payload.update(extra_payload)
        payload["metadata"] = {
            "message_source": message_source,
            **ui_defaults,
            **(payload.get("metadata") or {}),
        }
        sender('user_message', payload)
        return
    except Exception as e:
        from modules.goal_state_manager import GoalStateManager
        goal = GoalStateManager(web_terminal.data_dir, conversation_id)
        if goal.state["status"] in {"running", "reviewing", "retrying", "paused", "blocked"}:
            # Goal tasks must always own a TaskRecord, outcome and cancellation.
            raise
        debug_log(f"[CompletionNotice] 创建后台消息任务失败，回退直接执行: {e}")

    # 回退路径没有任务线程（未登记 TaskRecord），轮询器预占的门闸 token 无法随任务
    # 移交释放；必须在本函数内随回退执行结束释放，否则对话门闸会被长期占用，
    # 后续通知派发将恒被 try_acquire_main_task_gate 拒绝（2026-09-07 N1 修复）。
    # release_main_task_gate 对 None 或不匹配的 token 为无操作，可安全调用。
    try:
        payload = {
            'message': user_message,
            'conversation_id': conversation_id,
            'message_source': message_source,
            'timestamp': datetime.now().isoformat(),
        }
        payload.update(_user_message_ui_defaults(message_source, auto_user_message_event=True))
        payload.update(extra_payload)
        payload["metadata"] = {
            "message_source": message_source,
            "visibility": payload.get("visibility"),
            "starts_work": payload.get("starts_work"),
            **(payload.get("metadata") or {}),
        }
        sender('user_message', payload)
        try:
            from .chat_flow_task_main import handle_task_with_sender
            task_handle = asyncio.create_task(handle_task_with_sender(
                terminal=web_terminal,
                workspace=workspace,
                message=user_message,
                images=[],
                sender=sender,
                client_sid=client_sid,
                username=username,
                videos=[],
                auto_user_message_event=True,
            ))
            await task_handle
        except Exception as inner_exc:
            debug_log(f"[CompletionNotice] 回退处理 user_message 失败: {inner_exc}")
    finally:
        release_main_task_gate(web_terminal, main_task_gate_token)
