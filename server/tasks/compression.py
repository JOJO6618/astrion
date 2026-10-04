"""手动压缩使用普通任务的事件流、门闸及精确取消通道。"""
from __future__ import annotations

import asyncio

from modules.i18n import tr
from server.main_task_gate import acquire_adopted_main_task_gate, release_main_task_gate
from server.state import stop_flags


def run_compression_task(manager, rec, terminal, workspace, sender) -> None:
    from server.deep_compression import run_deep_compression

    token = acquire_adopted_main_task_gate(terminal, rec.directives.main_task_gate_token)
    if token is None:
        raise RuntimeError(tr("tasks.task_already_running"))
    loop = asyncio.new_event_loop()

    async def execute():
        if rec.stop_requested:
            raise asyncio.CancelledError()
        result = await run_deep_compression(
            web_terminal=terminal,
            workspace=workspace,
            conversation_id=rec.conversation_id,
            mode="manual",
            sender=sender,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or tr("tool_loop.deep_compression_failed"))
        # 引导语在压缩提交内写入；不重载 terminal，不启动模型续接。
        sender("task_complete", {
            "task_type": "compression",
            "preserve_pending_messages": True,
            **manager.get_conversation_running_status(terminal, rec.conversation_id),
        })

    try:
        asyncio.set_event_loop(loop)
        task = loop.create_task(execute())
        stop_flags[rec.task_id] = {
            "stop": rec.stop_requested,
            "task": task,
            "terminal": terminal,
            "loop": loop,
        }
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            rec.stop_requested = True
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        loop.close()
        release_main_task_gate(terminal, token)
