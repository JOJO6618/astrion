"""Branch-local summaries; the full message tree always remains authoritative."""
from copy import deepcopy
import hashlib
import json
from uuid import uuid4

from modules.chat_mode.tree import MessageTree
from modules.personalization_manager import load_personalization_config


def fingerprint(messages):
    return hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def select_summary(document, path):
    ids = [message["message_id"] for message in path]
    valid = [summary for summary in document["metadata"]["chat"].get("summaries", [])
             if summary.get("valid") and summary.get("message_ids")
             and ids[:len(summary["message_ids"])] == summary["message_ids"]]
    return max(valid, key=lambda summary: len(summary["message_ids"]), default=None)


def api_history(context):
    document = context.storage.read(context.current_conversation_id)
    summary = select_summary(document, context.conversation_history)
    raw = context.build_api_history()
    if summary:
        return [{"role": "system", "content": "前文对话摘要（以当前系统设置为准）：\n" + summary["content"]},
                *raw[len(summary["message_ids"]):]]
    return raw


def estimate_tokens(messages):
    """Trigger estimate over stored text/refs, never over base64 payload bytes."""
    total = 0
    for message in messages:
        text = json.dumps({key: message.get(key) for key in
                           ("content", "reasoning_content", "tool_calls")}, ensure_ascii=False)
        ascii_count = sum(ord(character) < 128 for character in text)
        total += (ascii_count + 2) // 3 + len(text) - ascii_count + 16
        for media in message.get("media_refs") or []:
            total += 2048 if media.get("kind") == "image" else 8192
    return total


async def maybe_compress(runtime, sender):
    context = runtime.context_manager
    context.refresh()
    document = runtime.storage.read(runtime.conversation_id)
    path = context.conversation_history
    existing = select_summary(document, path)
    estimated_path = path
    if existing:
        estimated_path = [{"content": existing["content"]}, *path[len(existing["message_ids"]):]]
    # Provider usage remains authoritative for billing and the context ring.
    size = estimate_tokens(estimated_path) + 4096
    preferences = load_personalization_config(runtime.preferences_dir)
    threshold = preferences["chat_deep_compress_trigger_tokens"]
    if size < threshold:
        return
    path = deepcopy(context.conversation_history)
    users = [index for index, message in enumerate(path) if message["role"] == "user"
             and message.get("metadata", {}).get("visibility") != "hidden"]
    if len(users) < 2:
        raise RuntimeError("This message exceeds the Chat context budget; shorten it or start a new Chat")
    cutoff = users[-2] if len(users) >= 3 else users[-1]
    prefix = path[:cutoff]
    if not prefix:
        return
    existing = select_summary(document, prefix)
    if existing and len(existing["message_ids"]) >= cutoff:
        raise RuntimeError("The remaining Chat input exceeds the model context budget")
    allowed, quota = runtime.record_model_call(runtime.thinking_mode)
    if not allowed:
        raise RuntimeError(f"Chat summary quota exceeded: {quota}")
    sender("compression_state", {"in_progress": True, "conversation_id": runtime.conversation_id})
    try:
        from modules.chat_mode.summarize import summarize
        # summarize accepts stored messages, converting media only once.
        source = prefix
        if existing:
            source = [{"role": "user", "content": existing["content"]}, *prefix[len(existing["message_ids"]):]]
        text = await summarize(runtime.storage, document, source, api_client=runtime.api_client)
        digest = fingerprint(prefix)
        ids = [message["message_id"] for message in prefix]

        def commit(current):
            tree = MessageTree(current)
            turn = tree.turn(runtime.turn_id)
            current_path = tree.path(turn["tip_id"] or turn["parent_id"], latest=False)
            current_prefix = current_path[:len(ids)]
            if [message["message_id"] for message in current_prefix] != ids or fingerprint(current_prefix) != digest:
                raise RuntimeError("Chat changed while summarizing; the original messages were preserved")
            tree.state["summaries"].append({"id": uuid4().hex, "valid": True,
                                             "message_ids": ids, "content": text})
        runtime.storage.manager.mutate_conversation(runtime.conversation_id, commit)
    finally:
        sender("compression_state", {"in_progress": False, "conversation_id": runtime.conversation_id})
