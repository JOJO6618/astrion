"""Explicit recovery after a worker has retired; ordinary reads never write."""
from __future__ import annotations

import json

from modules.chat_mode.tree import MessageTree, identifier


def recover_turn(storage, conversation_id, turn_id, status="failed"):
    """Caller must hold task admission ownership or have joined the worker.

    A crashed task is not restarted. Complete calls lacking a result receive an
    explicit cancellation result before the turn is unlocked for another send.
    """
    def mutate(document):
        tree = MessageTree(document)
        turn = tree.turn(turn_id)
        if turn["status"] != "running":
            return
        path = tree.path(turn["tip_id"] or turn["parent_id"], latest=False)
        pending = {}
        for message in path:
            if message["role"] == "assistant":
                for call in message.get("tool_calls") or []:
                    pending[call["id"]] = call.get("function", {}).get("name", "")
            elif message["role"] == "tool":
                pending.pop(message.get("tool_call_id"), None)
        for call_id, name in pending.items():
            tree.append_to_turn(turn_id, {
                "message_id": identifier(), "role": "tool", "tool_call_id": call_id,
                "name": name, "content": json.dumps({
                    "success": False, "status": "cancelled", "message": "执行已中断，未取得完成结果",
                }, ensure_ascii=False),
            })
        tree.finish_turn(turn_id, status)
    storage.manager.mutate_conversation(conversation_id, mutate)
