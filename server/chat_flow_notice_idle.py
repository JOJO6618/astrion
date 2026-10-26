from __future__ import annotations
from datetime import datetime
from typing import List, Optional
from server.runtime import InternalDirectives, RuntimeContext, TaskParams, runtime_service
from modules.multi_agent.debug_logger import ma_debug
from .chat_flow_task_support import _auto_message_type_for_multi_agent_subtype
from .chat_flow_message_policy import _user_message_ui_defaults


async def _dispatch_multi_agent_idle_messages(**kwargs):
    """Async compatibility entry; persistence and admission have no await points."""
    from server.conversation_view.snapshots import VIEW_LOCK
    with VIEW_LOCK:
        return _dispatch_multi_agent_idle_messages_sync(**kwargs)

def _dispatch_multi_agent_idle_messages_sync(
    *,
    web_terminal,
    workspace,
    sender,
    client_sid,
    username,
    conversation_id,
    messages: List[str],
    gate_token: Optional[str] = None,
):
    """主智能体空闲时，把多智能体 pending 消息作为新一轮用户消息分发。

    受理阶段只装配directives，不写历史或回显；认领门闸后再提交前N-1条。
    最后一条通过正常notice任务输入链持久化并触发主智能体工作。
    """
    ma_debug(
        "dispatch_ma_idle_enter",
        conversation_id=conversation_id,
        count=len(messages) if messages else 0,
        previews=[str(m)[:150] for m in (messages or [])],
    )
    if not messages:
        ma_debug("dispatch_ma_idle_no_messages", conversation_id=conversation_id)
        return

    from modules.multi_agent.state import parse_multi_agent_message
    from modules.goal_policy import goal_blocks_background
    if goal_blocks_background(web_terminal.data_dir, conversation_id):
        raise RuntimeError("Goal is paused; multi-agent notifications remain pending")
    from .tasks import task_manager

    parsed_messages = []
    for msg_text in messages:
        parsed = parse_multi_agent_message(msg_text) or {}
        parsed_messages.append({
            "text": msg_text,
            "display_name": parsed.get("display_name"),
            "subtype": parsed.get("subtype"),
        })

    last = parsed_messages[-1]
    message_source = "sub_agent"

    # 3) 最后一条触发新一轮工作
    ui_defaults = dict(_user_message_ui_defaults(message_source, auto_user_message_event=True))
    ui_defaults["starts_work"] = False

    workspace_id = getattr(workspace, "workspace_id", None) or "default"
    ma_auto_type = _auto_message_type_for_multi_agent_subtype(last["subtype"])
    # 重要：ui_defaults 默认会给出 visibility="compact"/starts_work=False，
    # 多智能体主消息是正常聊天消息，必须在 **ui_defaults 之后覆写为 chat，
    # 否则后端历史 metadata.visibility=compact 会让加载的消息走通知渲染。
    auto_payload = {
        **ui_defaults,
        "message_source": message_source,
        "sub_agent_notice": True,
        "multi_agent_message": True,
        "multi_agent_display_name": last["display_name"],
        "multi_agent_subtype": last["subtype"],
        "auto_message_type": ma_auto_type,
        "starts_work": False,
        "visibility": "chat",
        "timestamp": datetime.now().isoformat(),
    }
    ma_debug(
        "dispatch_ma_idle_before_create_task",
        conversation_id=conversation_id,
        last_text_preview=last["text"][:300],
        workspace_id=workspace_id,
        username=username,
    )
    auto_payload.setdefault("metadata", {
        **ui_defaults,
        "message_source": message_source,
        "auto_message_type": ma_auto_type,
        "multi_agent_message": True,
        "multi_agent_display_name": last["display_name"],
        "multi_agent_subtype": last["subtype"],
        "starts_work": False,
        "visibility": "chat",
    })
    preceding_list = None
    if len(parsed_messages) > 1:
        preceding_list = [
            {
                "message": item["text"],
                "payload": {
                    "message_id": item.get("message_id"),
                    "message_source": "sub_agent",
                    "sub_agent_notice": True,
                    "multi_agent_message": True,
                    "multi_agent_display_name": item["display_name"],
                    "multi_agent_subtype": item["subtype"],
                    "auto_message_type": _auto_message_type_for_multi_agent_subtype(item["subtype"]),
                    "visibility": "chat",
                    "starts_work": False,
                },
            }
            for item in parsed_messages[:-1]
        ]

    try:
        ctx = RuntimeContext.from_terminal(
            web_terminal,
            workspace,
            username,
            params=TaskParams(
                message=last["text"],
                conversation_id=conversation_id,
                message_source=message_source,
                model_key=getattr(web_terminal, "model_key", None),
                run_mode=getattr(web_terminal, "run_mode", None),
                thinking_mode=getattr(web_terminal, "thinking_mode", None),
                task_type="notice",
            ),
            directives=InternalDirectives(
                auto_user_message_event=True,
                auto_user_message_payload=auto_payload,
                preceding_user_notices=preceding_list,
                main_task_gate_token=gate_token,
            ),
        )
        rec = runtime_service.create_task(ctx)
    except Exception as exc:
        ma_debug(
            "dispatch_ma_idle_create_task_exception",
            conversation_id=conversation_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    task_id_created = getattr(rec, "task_id", None)
    ma_debug("dispatch_ma_idle_task_created", conversation_id=conversation_id, task_id=task_id_created)

    # 用户消息只由认领门闸后的持久化／任务事件链发布，受理阶段不乐观回显。
    ma_debug(
        "dispatch_ma_idle_exit_ok",
        conversation_id=conversation_id,
        count=len(parsed_messages),
        task_id=getattr(rec, "task_id", None),
    )
