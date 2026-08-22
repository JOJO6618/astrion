# ========== api_client.py ==========
# utils/api_client.py - OpenAI-compatible API 客户端（支持Web模式）

import httpx
import json
import asyncio
import base64
import mimetypes
import os
from typing import List, Dict, Optional, AsyncGenerator, Any
from pathlib import Path
from datetime import datetime
from pathlib import Path
from typing import Tuple
try:
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )

from utils.log_rotation import append_line, prune_dir



from utils.api_client.utils import _api_dump_enabled

class APIClientMessageMixin:
    def _merge_system_messages(self, messages: List[Dict]) -> List[Dict]:
        """
        仅合并最开头连续的 system 消息（系统提示），后续插入的 system 消息保持原样。
        """
        if not messages:
            return messages

        merged_contents: List[str] = []
        idx = 0
        while idx < len(messages) and messages[idx].get("role") == "system":
            content = messages[idx].get("content", "")
            if isinstance(content, str):
                merged_contents.append(content)
            else:
                merged_contents.append(json.dumps(content, ensure_ascii=False))
            idx += 1

        if not merged_contents:
            return messages

        merged = {
            "role": "system",
            "content": "\n\n".join(c for c in merged_contents if c)
        }
        return [merged] + messages[idx:]

    @staticmethod
    def _normalize_multimodal_capability(value: Any) -> str:
        text = str(value or "none").strip().lower()
        if text == "video,image":
            return "image,video"
        if text in {"none", "image", "video", "image,video"}:
            return text
        return "none"

    @staticmethod
    def _sanitize_content_for_capability(
        content: Any,
        *,
        supports_image: bool,
        supports_video: bool,
    ) -> Any:
        """根据模型多模态能力裁剪 content，避免向纯文本模型发送 image_url/video_url。"""
        if not isinstance(content, list):
            return content

        kept_parts: List[Dict[str, Any]] = []
        text_segments: List[str] = []
        dropped_media = False
        unsupported_media_notice = "当前模型无查看图片/视频能力，无法返回结果"

        for part in content:
            if not isinstance(part, dict):
                text = str(part).strip()
                if text:
                    text_segments.append(text)
                continue

            ctype = str(part.get("type") or "").strip().lower()
            if ctype == "text":
                text = str(part.get("text") or "")
                if text:
                    text_segments.append(text)
                    kept_parts.append({"type": "text", "text": text})
                continue
            if ctype == "image_url":
                if supports_image:
                    kept_parts.append(part)
                else:
                    dropped_media = True
                continue
            if ctype == "video_url":
                if supports_video:
                    kept_parts.append(part)
                else:
                    dropped_media = True
                continue

            # 其他未知/暂不支持类型统一忽略（纯文本模型下只保留 text）
            continue

        # 纯文本模型：统一回退为字符串，规避供应商对 content part 类型的严格校验
        if not supports_image and not supports_video:
            text_payload = "\n".join(seg for seg in text_segments if seg).strip()
            if dropped_media:
                if text_payload:
                    if unsupported_media_notice in text_payload:
                        return text_payload
                    return f"{text_payload}\n\n{unsupported_media_notice}"
                return unsupported_media_notice
            return text_payload

        # 半多模态模型（例如只支持图片）：尽量保留可支持的 part
        if kept_parts:
            if len(kept_parts) == 1 and kept_parts[0].get("type") == "text":
                return kept_parts[0].get("text", "")
            return kept_parts
        return "\n".join(seg for seg in text_segments if seg).strip()

    def _sanitize_messages_for_model_capability(self, messages: List[Dict]) -> List[Dict]:
        model_key = str(self.model_key or "").strip()
        supports_image = False
        supports_video = False
        capability_source = "profile_fallback"

        if model_key:
            try:
                from config.model_profiles import get_model_capabilities
                caps = get_model_capabilities(model_key)
                supports_image = bool(caps.get("supports_image"))
                supports_video = bool(caps.get("supports_video"))
                capability_source = "model_key"
            except Exception:
                capability_source = "profile_fallback"

        if capability_source != "model_key":
            multimodal = self._normalize_multimodal_capability(self.model_multimodal)
            supports_image = multimodal in {"image", "image,video"}
            supports_video = multimodal in {"video", "image,video"}

        if supports_image and supports_video:
            return messages

        changed = 0
        sanitized: List[Dict[str, Any]] = []
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            msg_copy = dict(message)
            original_content = msg_copy.get("content")
            new_content = self._sanitize_content_for_capability(
                original_content,
                supports_image=supports_image,
                supports_video=supports_video,
            )
            if new_content != original_content:
                changed += 1
            msg_copy["content"] = new_content
            sanitized.append(msg_copy)

        if changed:
            self._debug_log(
                {
                    "event": "sanitize_messages_for_model_capability",
                    "model_key": model_key,
                    "capability_source": capability_source,
                    "supports_image": supports_image,
                    "supports_video": supports_video,
                    "changed_messages": changed,
                }
            )
        return sanitized

    def _sanitize_message_fields_for_api(self, messages: List[Dict]) -> List[Dict]:
        """移除只供本地 UI/持久化使用、OpenAI-compatible API 不接受的消息字段。"""
        sanitized: List[Dict[str, Any]] = []
        stripped_fields: Dict[str, int] = {}

        allowed_common = {"role", "content", "name"}
        allowed_by_role = {
            "assistant": allowed_common | {"tool_calls", "reasoning_content"},
            "tool": allowed_common | {"tool_call_id"},
            "user": allowed_common,
            "system": allowed_common,
        }

        for message in messages or []:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").strip()
            allowed = allowed_by_role.get(role, allowed_common)
            clean: Dict[str, Any] = {}
            for key, value in message.items():
                if key not in allowed:
                    stripped_fields[key] = stripped_fields.get(key, 0) + 1
                    continue
                if key == "tool_calls" and not value:
                    stripped_fields[key] = stripped_fields.get(key, 0) + 1
                    continue
                clean[key] = value
            sanitized.append(clean)

        if stripped_fields:
            self._debug_log(
                {
                    "event": "sanitize_message_fields_for_api",
                    "stripped_fields": stripped_fields,
                }
            )
        return sanitized

    def _build_content_with_images(self, text: str, images: List[str], videos: Optional[List[Any]] = None) -> Any:
        """将文本与图片/视频路径拼成多模态 content（用于 tool 消息）。"""
        videos = videos or []
        if not images and not videos:
            return text
        parts: List[Dict[str, Any]] = []
        extra_videos: List[Any] = []
        if text:
            parts.append({"type": "text", "text": text})
        base_path = Path(self.project_path or ".")
        for path in images:
            try:
                abs_path = (base_path / path).resolve()
                if not abs_path.exists() or not abs_path.is_file():
                    continue
                mime, _ = mimetypes.guess_type(abs_path.name)
                if mime and mime.startswith("video/"):
                    extra_videos.append(path)
                    continue
                if mime and not mime.startswith("image/"):
                    continue
                if not mime:
                    mime = "image/png"
                data = abs_path.read_bytes()
                b64 = base64.b64encode(data).decode("utf-8")
                parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
            except Exception:
                continue
        for item in [*videos, *extra_videos]:
            try:
                if isinstance(item, dict):
                    path = item.get("path") or ""
                else:
                    path = item
                if not path:
                    continue
                abs_path = (base_path / path).resolve()
                if not abs_path.exists() or not abs_path.is_file():
                    continue
                mime, _ = mimetypes.guess_type(abs_path.name)
                if not mime:
                    mime = "video/mp4"
                data = abs_path.read_bytes()
                b64 = base64.b64encode(data).decode("utf-8")
                payload: Dict[str, Any] = {
                    "type": "video_url",
                    "video_url": {"url": f"data:{mime};base64,{b64}"}
                }
                if isinstance(item, dict) and item.get("fps") is not None:
                    payload["fps"] = item.get("fps")
                parts.append(payload)
            except Exception:
                continue
        return parts if parts else text
        
        if read_type == "extract":
            segments = data.get("segments", [])
            header = (
                f"从 {path} 抽取 {len(segments)} 个片段 {max_note}{truncated_note}"
            ).strip()
            seg_texts = []
            for idx, segment in enumerate(segments, 1):
                seg_note = "（片段截断）" if segment.get("truncated") else ""
                label = segment.get("label") or f"segment_{idx}"
                snippet = segment.get("content", "")
                seg_texts.append(
                    f"[{label}] 行 {segment.get('line_start')}~{segment.get('line_end')}{seg_note}\n```\n{snippet}\n```"
                )
            if not seg_texts:
                seg_texts.append("未提供可抽取的片段。")
            return "\n".join([header] + seg_texts)
        
        return json.dumps(data, ensure_ascii=False)
