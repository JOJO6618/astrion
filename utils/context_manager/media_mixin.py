# utils/context_manager.py - 上下文管理器（集成对话持久化和Token统计）

import os
import json
import base64
import mimetypes
import io
import uuid
import platform
import shutil
import subprocess
from copy import deepcopy
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime
try:
    from config import (
        MAX_CONTEXT_SIZE,
        DATA_DIR,
        PROMPTS_DIR,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
    )
    from config.model_profiles import (
        get_model_prompt_replacements,
        get_registered_model_keys,
        model_supports_image,
        model_supports_video,
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        MAX_CONTEXT_SIZE,
        DATA_DIR,
        PROMPTS_DIR,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
    )
    from config.model_profiles import (
        get_model_prompt_replacements,
        get_registered_model_keys,
        model_supports_image,
        model_supports_video,
    )
from utils.conversation_manager import ConversationManager
from utils.host_workspace_debug import write_host_workspace_debug
from utils.media_store import MediaStore
from utils.token_usage import normalize_usage_payload

AUTO_SHALLOW_PLACEHOLDER = "过早的工具结果已经被自动压缩"
AUTO_SHALLOW_TOOL_WHITELIST = {
    "write_file",
    "read_file",
    "edit_file",
    "terminal_input",
    "terminal_snapshot",
    "web_search",
    "extract_webpage",
    "run_command",
    "view_image",
    "view_video",
}


class MediaMixin:
    """ContextManager media mixin 能力 mixin。"""

    # -- 媒体读取统一经 selected backend.files（不碰控制端 Path.resolve/exists） --
    def _media_files_access(self) -> Any:
        """当前上下文选中的执行后端文件视图（主终端后端 > 绑定后端 > 明确本机）。"""
        from utils.context_manager.fs_access import files_for_context

        try:
            return files_for_context(self)
        except Exception:
            return None

    def _mapped_media_path_text(self, raw_path: Any) -> Optional[str]:
        """对话迁移映射后的路径文本；纯字符串映射，不做本地 resolve/exists。

        别名只允许导入材料目录下的相对路径；最终是否可读、是否是普通文件、
        是否有符号链接全部交给执行后端（FileManager 的校验/no-follow）。
        """
        path_text = str(raw_path or "").strip()
        if not path_text:
            return None
        metadata = getattr(self, "conversation_metadata", {}) or {}
        aliases = (metadata.get("conversation_transfer") or {}).get("paths") or {}
        relative = aliases.get(path_text)
        if isinstance(relative, str):
            if (
                not relative.startswith(".astrion/conversation_assets/")
                or ".." in relative.split("/")
                or "\\" in relative
            ):
                return None
            project_path = getattr(self, "project_path", None)
            if project_path is None:
                return None
            return str(Path(project_path) / relative)
        return path_text

    @staticmethod
    def _media_mime(meta: Dict[str, Any], path_text: str) -> str:
        """从后端元数据/路径后缀推断 MIME；纯字符串推断，不读本地文件。"""
        candidates: List[str] = []
        suffix = str(meta.get("suffix") or "").strip()
        if suffix:
            candidates.append(f"file{suffix if suffix.startswith('.') else '.' + suffix}")
        candidates.append(str(meta.get("path") or ""))
        candidates.append(str(path_text or ""))
        for candidate in candidates:
            if not candidate:
                continue
            mime, _ = mimetypes.guess_type(candidate)
            if mime:
                return mime
        return ""

    @staticmethod
    def _media_meta_readable(meta: Dict[str, Any]) -> bool:
        """后端元数据是否允许继续读取；容器元数据缺口交给 read_binary 判定。"""
        if not isinstance(meta, dict):
            return False
        if meta.get("code") == "metadata_unsupported_in_container":
            return True
        return bool(meta.get("success") and meta.get("exists") and meta.get("is_file"))

    def _store_media_item_from_candidate(self, candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(candidate, dict):
            return None
        kind = str(candidate.get("kind") or "").strip().lower() or "binary"
        mime_type = candidate.get("mime_type")
        source_path = candidate.get("source_path")
        base_ref: Optional[Dict[str, Any]] = None
        try:
            media_id = str(candidate.get("media_id") or "").strip()
            if media_id:
                data_url = self.media_store.to_data_url(candidate)
                if data_url:
                    base_ref = {
                        "media_id": media_id,
                        "kind": kind,
                        "mime_type": mime_type,
                        "store_path": candidate.get("store_path"),
                        "source_path": source_path,
                    }
                else:
                    base_ref = None
            elif candidate.get("data_url"):
                stored = self.media_store.store_data_url(
                    str(candidate.get("data_url")),
                    kind=kind,
                    source_path=source_path,
                )
                base_ref = dict(stored)
            elif candidate.get("data_base64") or candidate.get("data"):
                payload = str(candidate.get("data_base64") or candidate.get("data") or "")
                if payload:
                    stored = self.media_store.store_base64_data(
                        payload,
                        kind=kind,
                        mime_type=mime_type,
                        source_path=source_path,
                    )
                    base_ref = dict(stored)
            elif candidate.get("path"):
                path_text = self._mapped_media_path_text(candidate.get("path"))
                files_access = self._media_files_access()
                if path_text and files_access is not None:
                    from utils.context_manager.fs_access import inspect_meta

                    meta = inspect_meta(files_access, path_text)
                    if self._media_meta_readable(meta):
                        # 内容从执行后端读取，持久 blob 缓存仍写控制端明确可信目录
                        # （MediaStore 自己的 data_dir），不做任何本地路径回退。
                        payload = files_access.read_binary(path_text)
                        if isinstance(payload, (bytes, bytearray)) and payload:
                            stored = self.media_store.store_bytes(
                                bytes(payload),
                                kind=kind,
                                mime_type=mime_type or self._media_mime(meta, path_text) or None,
                                source_path=source_path or str(candidate.get("path")),
                            )
                            base_ref = dict(stored)
        except Exception:
            base_ref = None
        if not base_ref:
            return None
        # 保留调用侧附加信息（例如视频 fps、MCP item 类型等）
        passthrough_keys = {
            "label",
            "fps",
            "source",
            "origin",
            "item_type",
            "name",
            "title",
            "uri",
            "url",
            "index",
        }
        for key in passthrough_keys:
            if key in candidate and candidate.get(key) is not None:
                base_ref[key] = candidate.get(key)
        if base_ref.get("source_path") is None and source_path:
            base_ref["source_path"] = source_path
        return base_ref

    def _prepare_media_candidates(
        self,
        images: Optional[List[Any]] = None,
        videos: Optional[List[Any]] = None,
        media_refs: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for item in media_refs or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip().lower()
            if not kind:
                mime_hint = str(item.get("mime_type") or item.get("mimeType") or "").strip()
                if mime_hint.startswith("image/"):
                    kind = "image"
                elif mime_hint.startswith("video/"):
                    kind = "video"
                elif mime_hint.startswith("audio/"):
                    kind = "audio"
            candidate = dict(item)
            if kind:
                candidate["kind"] = kind
            if "mimeType" in candidate and "mime_type" not in candidate:
                candidate["mime_type"] = candidate.get("mimeType")
            candidates.append(candidate)

        for path in images or []:
            if isinstance(path, dict):
                candidate = dict(path)
                candidate.setdefault("kind", "image")
                if "mimeType" in candidate and "mime_type" not in candidate:
                    candidate["mime_type"] = candidate.get("mimeType")
                if "path" not in candidate and path.get("url"):
                    candidate["path"] = path.get("url")
                candidates.append(candidate)
                continue
            path_text = str(path or "").strip()
            if not path_text:
                continue
            candidates.append(
                {
                    "kind": "image",
                    "path": path_text,
                    "source_path": path_text,
                    "source": "message_images",
                }
            )

        for item in videos or []:
            if isinstance(item, dict):
                path_text = str(item.get("path") or "").strip()
                if not path_text:
                    continue
                candidate = {
                    "kind": "video",
                    "path": path_text,
                    "source_path": path_text,
                    "fps": item.get("fps"),
                    "source": item.get("source") or "message_videos",
                }
                if item.get("mime_type"):
                    candidate["mime_type"] = item.get("mime_type")
                elif item.get("mimeType"):
                    candidate["mime_type"] = item.get("mimeType")
                candidates.append(candidate)
            else:
                path_text = str(item or "").strip()
                if not path_text:
                    continue
                candidates.append(
                    {
                        "kind": "video",
                        "path": path_text,
                        "source_path": path_text,
                        "source": "message_videos",
                    }
                )
        return candidates

    @staticmethod
    def _dedupe_media_refs(media_refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for item in media_refs:
            if not isinstance(item, dict):
                continue
            media_id = str(item.get("media_id") or "").strip()
            source_path = str(item.get("source_path") or "").strip()
            key = (media_id, source_path, str(item.get("kind") or ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _compress_image_if_needed(self, data: bytes, name: str = "") -> Optional[str]:
        """根据个性化设置压缩图片，返回 data URL（若压缩失败则返回 None 表示使用原图）。

        输入是执行后端读到的字节，不是控制端本地路径。
        """
        mode = getattr(self, "image_compression_mode", "original") or "original"
        if mode == "original":
            return None
        target_map = {
            "1080p": (1920, 1080),
            "720p": (1280, 720),
            "540p": (960, 540),
        }
        target = target_map.get(mode)
        if not target:
            return None
        try:
            from PIL import Image
        except Exception:
            return None
        try:
            with Image.open(io.BytesIO(bytes(data))) as im:
                w, h = im.size
                max_w, max_h = target
                if w <= max_w and h <= max_h:
                    return None  # 已经不超过目标，不压缩
                im_copy = im.copy()
                im_copy.thumbnail((max_w, max_h))
                buf = io.BytesIO()
                im_copy.save(buf, format="PNG", optimize=True)
                compressed = buf.getvalue()
                mime, _ = mimetypes.guess_type(str(name or ""))
                if not mime:
                    mime = "image/png"
                b64 = base64.b64encode(compressed).decode("utf-8")
                return f"data:{mime};base64,{b64}"
        except Exception:
            return None

    def _build_content_with_images(
        self,
        text: str,
        images: List[Any],
        videos: Optional[List[Any]] = None,
        media_refs: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """将文本与媒体组合成多模态 content。

        优先使用 media_refs（来自 media_store），回退到历史路径字段（images/videos）。
        """
        videos = videos or []
        media_refs = media_refs or []
        if not images and not videos and not media_refs:
            return text

        parts: List[Dict[str, Any]] = []
        extra_videos: List[Any] = []
        extra_notes: List[str] = []
        media_payload_attached = False

        if media_refs:
            for ref in media_refs:
                if not isinstance(ref, dict):
                    continue
                kind = str(ref.get("kind") or "").strip().lower()
                data_url = self.media_store.to_data_url(ref)
                if not data_url:
                    continue
                if kind == "image":
                    parts.append({"type": "image_url", "image_url": {"url": data_url}})
                    media_payload_attached = True
                    continue
                if kind == "video":
                    payload: Dict[str, Any] = {"type": "video_url", "video_url": {"url": data_url}}
                    fps_value = ref.get("fps")
                    if fps_value is not None:
                        payload["fps"] = fps_value
                    parts.append(payload)
                    media_payload_attached = True
                    continue
                if kind == "audio":
                    mime = str(ref.get("mime_type") or "audio/*")
                    size = ref.get("size")
                    label = f"[已附加音频 {mime}"
                    if isinstance(size, int):
                        label += f", {size} bytes"
                    label += "]"
                    extra_notes.append(label)
                    media_payload_attached = True

        # 若已通过 media_refs 成功附加媒体，避免再次从路径重复注入
        should_use_legacy_paths = not media_payload_attached

        if should_use_legacy_paths:
            from utils.context_manager.fs_access import inspect_meta

            files_access = self._media_files_access()
            for item in images:
                try:
                    if isinstance(item, dict):
                        raw_path = item.get("path") or ""
                    else:
                        raw_path = item
                    path_text = self._mapped_media_path_text(raw_path)
                    if not path_text or files_access is None:
                        continue
                    meta = inspect_meta(files_access, path_text)
                    if not self._media_meta_readable(meta):
                        continue
                    mime = self._media_mime(meta, path_text)
                    if mime.startswith("video/"):
                        extra_videos.append(item)
                        continue
                    if mime and not mime.startswith("image/"):
                        continue
                    data = files_access.read_binary(path_text)
                    if not isinstance(data, (bytes, bytearray)) or not data:
                        continue
                    data_url = self._compress_image_if_needed(bytes(data), Path(path_text).name)
                    if not data_url:
                        if not mime:
                            mime = "image/png"
                        b64 = base64.b64encode(bytes(data)).decode("utf-8")
                        data_url = f"data:{mime};base64,{b64}"
                    parts.append({"type": "image_url", "image_url": {"url": data_url}})
                except Exception:
                    continue

            for item in [*videos, *extra_videos]:
                try:
                    if isinstance(item, dict):
                        raw_path = item.get("path") or ""
                    else:
                        raw_path = item
                    path_text = self._mapped_media_path_text(raw_path)
                    if not path_text or files_access is None:
                        continue
                    meta = inspect_meta(files_access, path_text)
                    if not self._media_meta_readable(meta):
                        continue
                    size = meta.get("size")
                    if isinstance(size, int) and size > 50 * 1024 * 1024:
                        continue
                    mime = self._media_mime(meta, path_text) or "video/mp4"
                    data = files_access.read_binary(path_text)
                    if not isinstance(data, (bytes, bytearray)) or not data:
                        continue
                    b64 = base64.b64encode(bytes(data)).decode("utf-8")
                    data_url = f"data:{mime};base64,{b64}"
                    payload = {"type": "video_url", "video_url": {"url": data_url}}
                    fps_value = item.get("fps") if isinstance(item, dict) else None
                    if fps_value is not None:
                        payload["fps"] = fps_value
                    parts.append(payload)
                except Exception:
                    continue

        text_segments = []
        if text:
            text_segments.append(text)
        if extra_notes:
            text_segments.extend(extra_notes)
        merged_text = "\n".join(segment for segment in text_segments if segment)
        if merged_text:
            parts.insert(0, {"type": "text", "text": merged_text})

        # 只有纯文本时保持字符串，避免无意义地返回 list[{"type":"text"}]
        if not parts:
            return text
        if len(parts) == 1 and parts[0].get("type") == "text":
            return parts[0].get("text", "")
        return parts
