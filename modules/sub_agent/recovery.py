"""Restore new child histories, completing only genuinely interrupted tool batches."""
import json
from pathlib import Path


def restore_child_context(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Child context is missing")
    # A process crash or explicit termination can interrupt the final batch.
    # Budget expiry never reaches this branch: normal rounds save real results.
    last_assistant = next((index for index in range(len(messages) - 1, -1, -1)
                           if messages[index].get("role") == "assistant"), None)
    if last_assistant is not None:
        calls = messages[last_assistant].get("tool_calls") or []
        returned = {message.get("tool_call_id") for message in messages[last_assistant + 1:]
                    if message.get("role") == "tool"}
        for call in calls:
            if call["id"] not in returned:
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": "工作进程在此工具结果回填前中断；执行结果未知，请核实实际状态。"})
    return messages
