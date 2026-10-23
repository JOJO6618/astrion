"""Inject accepted guidance only at complete response/tool boundaries."""
from __future__ import annotations

from server.attachment_paths import build_file_paths_message, build_media_paths_message
from server.chat_flow_task_support import inject_runtime_user_message


def inject_pending_guidance(*, web_terminal, messages, username, task_id, sender,
                            conversation_id, close_if_empty=False):
    from server.tasks import task_manager

    items = task_manager.consume_runtime_guidance_for_injection(
        username, task_id, close_if_empty=close_if_empty)
    count = 0
    for raw in items:
        item = raw if isinstance(raw, dict) else {"text": str(raw or "")}
        text = str(item.get("text") or "").strip()
        images, videos = item.get("images") or [], item.get("videos") or []
        files = item.get("files") or []
        media_notice = build_media_paths_message(images, videos)
        file_notice = build_file_paths_message(files)
        if not text and not media_notice and not file_notice:
            continue
        inject_runtime_user_message(
            web_terminal=web_terminal, messages=messages, text=text or "请查看本次附带的附件。",
            source=item.get("source") or "guidance", sender=sender,
            conversation_id=conversation_id, inline=True, images=images, videos=videos,
            extra_metadata={"files": files, "media_paths_only": bool(item.get("media_paths_only"))},
        )
        for source, notice in (("media_paths", media_notice), ("file_paths", file_notice)):
            if notice:
                # The ordinary input builders already supply the notification prefix.
                inject_runtime_user_message(
                    web_terminal=web_terminal, messages=messages,
                    text=notice.split("\n", 1)[1], source=source, sender=sender,
                    conversation_id=conversation_id, inline=True,
                )
        count += 1
    return count
