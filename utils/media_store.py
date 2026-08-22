"""对话媒体存储：将图片/视频/音频二进制与对话 JSON 解耦。"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.atomic_io import replace_with_retry


DEFAULT_MIME_BY_KIND: Dict[str, str] = {
    "image": "image/png",
    "video": "video/mp4",
    "audio": "audio/wav",
}


def _normalize_kind(value: Optional[str], mime_type: Optional[str] = None) -> str:
    kind = str(value or "").strip().lower()
    if kind in {"image", "video", "audio"}:
        return kind
    mime = str(mime_type or "").strip().lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "binary"


def _normalize_mime(value: Optional[str], kind: str, fallback_path: Optional[str] = None) -> str:
    mime = str(value or "").strip().lower()
    if mime:
        return mime
    if fallback_path:
        guessed, _ = mimetypes.guess_type(str(fallback_path))
        if guessed:
            return guessed
    return DEFAULT_MIME_BY_KIND.get(kind, "application/octet-stream")


def _guess_extension(mime_type: str, kind: str) -> str:
    ext = mimetypes.guess_extension(mime_type or "")
    if ext:
        return ext
    if kind == "image":
        return ".png"
    if kind == "video":
        return ".mp4"
    if kind == "audio":
        return ".wav"
    return ".bin"


def _decode_base64_payload(payload: str) -> bytes:
    data = "".join(str(payload or "").strip().split())
    if not data:
        raise ValueError("base64 数据为空")
    missing_padding = len(data) % 4
    if missing_padding:
        data += "=" * (4 - missing_padding)
    return base64.b64decode(data.encode("utf-8"), validate=False)


def _parse_data_url(data_url: str) -> Tuple[str, str]:
    raw = str(data_url or "").strip()
    if not raw.startswith("data:") or "," not in raw:
        raise ValueError("非法 data URL")
    header, payload = raw.split(",", 1)
    mime = "application/octet-stream"
    if ";" in header:
        mime = header[5:].split(";", 1)[0] or mime
    elif len(header) > 5:
        mime = header[5:]
    if ";base64" not in header.lower():
        raise ValueError("仅支持 base64 data URL")
    return mime, payload


class MediaStore:
    """媒体存储中心（data/conversations/media_store）。"""

    INDEX_VERSION = 1

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.conversations_dir = self.data_dir / "conversations"
        self.root_dir = self.conversations_dir / "media_store"
        self.blobs_dir = self.root_dir / "blobs"
        self.index_file = self.root_dir / "index.json"
        self._lock = threading.RLock()
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.conversations_dir.mkdir(parents=True, exist_ok=True)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_file.exists():
            self._save_index(
                {
                    "version": self.INDEX_VERSION,
                    "media": {},
                    "message_map": {},
                    "updated_at": datetime.now().isoformat(),
                }
            )

    def _atomic_write_json(self, target_file: Path, payload: Dict[str, Any]) -> None:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(target_file.parent),
                prefix=f".{target_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as fh:
                temp_path = Path(fh.name)
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            # Windows 瞬时持锁（并发读取/杀软扫描）重试，POSIX 行为不变
            replace_with_retry(temp_path, target_file)
        finally:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    def _load_index(self) -> Dict[str, Any]:
        try:
            if not self.index_file.exists():
                return {
                    "version": self.INDEX_VERSION,
                    "media": {},
                    "message_map": {},
                    "updated_at": datetime.now().isoformat(),
                }
            data = json.loads(self.index_file.read_text(encoding="utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError("index.json 不是对象")
            data.setdefault("version", self.INDEX_VERSION)
            data.setdefault("media", {})
            data.setdefault("message_map", {})
            data.setdefault("updated_at", datetime.now().isoformat())
            if not isinstance(data.get("media"), dict):
                data["media"] = {}
            if not isinstance(data.get("message_map"), dict):
                data["message_map"] = {}
            return data
        except Exception:
            return {
                "version": self.INDEX_VERSION,
                "media": {},
                "message_map": {},
                "updated_at": datetime.now().isoformat(),
            }

    def _save_index(self, payload: Dict[str, Any]) -> None:
        payload["updated_at"] = datetime.now().isoformat()
        self._atomic_write_json(self.index_file, payload)

    @staticmethod
    def _build_media_id(sha256_hex: str) -> str:
        return f"sha256:{sha256_hex}"

    def _blob_path_for(self, sha256_hex: str, ext: str) -> Tuple[Path, str]:
        rel = Path("blobs") / sha256_hex[:2] / f"{sha256_hex}{ext}"
        return self.root_dir / rel, rel.as_posix()

    def _write_blob_if_missing(self, blob_path: Path, payload: bytes) -> None:
        if blob_path.exists():
            return
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=str(blob_path.parent),
                prefix=f".{blob_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as fh:
                temp_path = Path(fh.name)
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            # Windows 瞬时持锁重试，POSIX 行为不变
            replace_with_retry(temp_path, blob_path)
        finally:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    def store_bytes(
        self,
        payload: bytes,
        *,
        kind: Optional[str] = None,
        mime_type: Optional[str] = None,
        source_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(payload, (bytes, bytearray)) or len(payload) == 0:
            raise ValueError("媒体二进制为空")
        sha256_hex = hashlib.sha256(payload).hexdigest()
        media_id = self._build_media_id(sha256_hex)
        normalized_kind = _normalize_kind(kind, mime_type)
        normalized_mime = _normalize_mime(mime_type, normalized_kind, fallback_path=source_path)
        ext = _guess_extension(normalized_mime, normalized_kind)
        blob_path, blob_rel = self._blob_path_for(sha256_hex, ext)

        with self._lock:
            self._write_blob_if_missing(blob_path, bytes(payload))
            index = self._load_index()
            media = index.setdefault("media", {})
            entry = media.get(media_id) if isinstance(media.get(media_id), dict) else {}
            entry.update(
                {
                    "media_id": media_id,
                    "sha256": sha256_hex,
                    "kind": normalized_kind,
                    "mime_type": normalized_mime,
                    "size": len(payload),
                    "blob_rel_path": blob_rel,
                    "created_at": entry.get("created_at") or datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
            )
            media[media_id] = entry
            self._save_index(index)
        return {
            "media_id": media_id,
            "kind": normalized_kind,
            "mime_type": normalized_mime,
            "size": len(payload),
            "sha256": sha256_hex,
            "store_path": f"media_store/{blob_rel}",
            "source_path": source_path,
        }

    def store_file(
        self,
        file_path: Path,
        *,
        kind: Optional[str] = None,
        mime_type: Optional[str] = None,
        source_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        path = Path(file_path).expanduser().resolve()
        payload = path.read_bytes()
        return self.store_bytes(
            payload,
            kind=kind,
            mime_type=mime_type,
            source_path=source_path or str(file_path),
        )

    def store_base64_data(
        self,
        data_base64: str,
        *,
        kind: Optional[str] = None,
        mime_type: Optional[str] = None,
        source_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = _decode_base64_payload(data_base64)
        return self.store_bytes(
            payload,
            kind=kind,
            mime_type=mime_type,
            source_path=source_path,
        )

    def store_data_url(
        self,
        data_url: str,
        *,
        kind: Optional[str] = None,
        source_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        mime_type, base64_payload = _parse_data_url(data_url)
        return self.store_base64_data(
            base64_payload,
            kind=kind,
            mime_type=mime_type,
            source_path=source_path,
        )

    def _resolve_blob_path(self, media_ref: Dict[str, Any]) -> Optional[Tuple[Path, Dict[str, Any]]]:
        media_id = str(media_ref.get("media_id") or "").strip()
        with self._lock:
            index = self._load_index()
            media = index.get("media") if isinstance(index.get("media"), dict) else {}
            entry = media.get(media_id) if media_id else None
            if isinstance(entry, dict):
                rel = str(entry.get("blob_rel_path") or "").strip()
                if rel:
                    path = self.root_dir / rel
                    if path.exists() and path.is_file():
                        return path, entry
            store_path = str(media_ref.get("store_path") or "").strip()
            if store_path.startswith("media_store/"):
                candidate = self.conversations_dir / store_path
            elif store_path:
                candidate = self.root_dir / store_path
            else:
                candidate = None
            if candidate and candidate.exists() and candidate.is_file():
                if not isinstance(entry, dict):
                    entry = {}
                return candidate, entry
        return None

    def get_media_entry(self, media_id: str) -> Optional[Dict[str, Any]]:
        target_id = str(media_id or "").strip()
        if not target_id:
            return None
        with self._lock:
            index = self._load_index()
            media = index.get("media") if isinstance(index.get("media"), dict) else {}
            entry = media.get(target_id)
            if isinstance(entry, dict):
                return dict(entry)
            return None

    def load_bytes(self, media_ref: Dict[str, Any]) -> Optional[bytes]:
        resolved = self._resolve_blob_path(media_ref or {})
        if not resolved:
            return None
        blob_path, _entry = resolved
        try:
            return blob_path.read_bytes()
        except Exception:
            return None

    def load_bytes_by_media_id(self, media_id: str) -> Optional[bytes]:
        target_id = str(media_id or "").strip()
        if not target_id:
            return None
        entry = self.get_media_entry(target_id)
        if not isinstance(entry, dict):
            return None
        ref = {
            "media_id": target_id,
            "store_path": entry.get("blob_rel_path"),
            "kind": entry.get("kind"),
            "mime_type": entry.get("mime_type"),
        }
        return self.load_bytes(ref)

    def to_data_url(self, media_ref: Dict[str, Any]) -> Optional[str]:
        payload = self.load_bytes(media_ref)
        if payload is None:
            return None
        mime = _normalize_mime(
            media_ref.get("mime_type"),
            _normalize_kind(media_ref.get("kind"), media_ref.get("mime_type")),
        )
        b64 = base64.b64encode(payload).decode("utf-8")
        return f"data:{mime};base64,{b64}"

    def link_message_media(
        self,
        conversation_id: Optional[str],
        message_id: Optional[str],
        media_refs: List[Dict[str, Any]],
    ) -> None:
        conv = str(conversation_id or "").strip()
        msg = str(message_id or "").strip()
        if not conv or not msg or not media_refs:
            return
        media_ids = [str((item or {}).get("media_id") or "").strip() for item in media_refs]
        media_ids = [item for item in media_ids if item]
        if not media_ids:
            return
        with self._lock:
            index = self._load_index()
            message_map = index.setdefault("message_map", {})
            conv_map = message_map.get(conv) if isinstance(message_map.get(conv), dict) else {}
            conv_map[msg] = {
                "media_ids": media_ids,
                "updated_at": datetime.now().isoformat(),
            }
            message_map[conv] = conv_map
            self._save_index(index)
