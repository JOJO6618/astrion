"""Versioned conversation archives; original documents remain lossless JSON."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import stat
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED

from .assets import MAX_BYTES, MAX_FILES, collect

FORMAT = "astrion-conversation"
VERSION = 1
MAX_DOCUMENT = 32 * 1024 * 1024


def validate_document(document: dict) -> str:
    if not isinstance(document, dict) or not isinstance(document.get("metadata"), dict):
        raise ValueError("Invalid conversation document")
    if not isinstance(document.get("messages"), list):
        raise ValueError("Conversation messages must be a list")
    if not isinstance(document.get("id"), str) or not document["id"]:
        raise ValueError("Conversation identity is missing")
    for message in document["messages"]:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant", "system", "tool"}:
            raise ValueError("Invalid conversation message")
    kind = document["metadata"].get("conversation_kind", "agent")
    if kind not in {"agent", "chat"}:
        raise ValueError("Unknown conversation kind")
    if kind == "chat":
        from modules.chat_mode.tree import MessageTree
        MessageTree(deepcopy(document))
    return kind


def export_archive(document: dict, data_dir: Path, project: Path | None, source: dict,
                   *, extra_resources=(), extra_files=None):
    kind = validate_document(document)
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_DOCUMENT:
        raise ValueError("Conversation document exceeds the 32 MiB export limit")
    resources, files = collect(document, data_dir, project)
    resources.extend(extra_resources)
    files.update(extra_files or {})
    if (len(files) > MAX_FILES or len(resources) > MAX_FILES
            or sum(map(len, files.values())) > MAX_BYTES):
        raise ValueError("Conversation resources exceed the export limit")
    manifest = {"format": FORMAT, "version": VERSION, "kind": kind,
                "document_sha256": hashlib.sha256(encoded).hexdigest(),
                "source": source, "resources": resources}
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("conversation.json", encoded)
        for name, payload in files.items():
            archive.writestr(name, payload)
    output.seek(0)
    return output


class _ArchiveInput:
    """Expose ZipFile's read protocol without copying a multipart upload.

    Python 3.9 SpooledTemporaryFile supports random access but lacks seekable,
    which ZipFile requires when opening individual archive entries.
    """
    def __init__(self, stream):
        self.stream = stream

    def read(self, size=-1):
        return self.stream.read(size)

    def seek(self, offset, whence=0):
        return self.stream.seek(offset, whence)

    def tell(self):
        return self.stream.tell()

    def seekable(self):
        capability = getattr(self.stream, "seekable", None)
        if callable(capability):
            return capability()
        # ZipFile has already verified random access while reading its directory.
        return True


def read_archive(stream):
    try:
        archive = ZipFile(_ArchiveInput(stream))
    except (BadZipFile, OSError, ValueError) as exc:
        raise ValueError("This is not an Astrion conversation file") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_FILES + 2 or sum(item.file_size for item in entries) > MAX_BYTES + MAX_DOCUMENT:
            raise ValueError("Conversation archive is too large")
        names = set()
        for item in entries:
            path = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            if (item.filename in names or path.is_absolute() or ".." in path.parts
                    or "\\" in item.filename or item.is_dir() or stat.S_ISLNK(mode)
                    or item.flag_bits & 1):
                raise ValueError("Invalid conversation archive entry")
            names.add(item.filename)
        if not {"manifest.json", "conversation.json"}.issubset(names):
            raise ValueError("Conversation archive is incomplete")
        if archive.getinfo("manifest.json").file_size > 4 * 1024 * 1024:
            raise ValueError("Conversation manifest is too large")
        if archive.getinfo("conversation.json").file_size > MAX_DOCUMENT:
            raise ValueError("Conversation document is too large")
        try:
            manifest = json.loads(archive.read("manifest.json"))
            encoded = archive.read("conversation.json")
            document = json.loads(encoded)
        except (ValueError, UnicodeError, BadZipFile) as exc:
            raise ValueError("Conversation JSON is invalid") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
            raise ValueError("Unsupported Astrion conversation file version")
        kind = validate_document(document)
        if manifest.get("kind") != kind or hashlib.sha256(encoded).hexdigest() != manifest.get("document_sha256"):
            raise ValueError("Conversation document checksum failed")
        resources = manifest.get("resources")
        source = manifest.get("source")
        if not isinstance(resources, list) or len(resources) > MAX_FILES or not isinstance(source, dict):
            raise ValueError("Invalid conversation manifest")
        files = {}
        for row in resources:
            if not isinstance(row, dict) or row.get("kind") not in {"image", "video", "material"}:
                raise ValueError("Invalid conversation resource")
            digest = row.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Invalid resource checksum")
            name = f"assets/{digest}"
            paths = row.get("paths")
            if row.get("file") != name or name not in names or not isinstance(paths, list) or not all(isinstance(p, str) and len(p) <= 8192 for p in paths):
                raise ValueError("Invalid resource reference")
            if row.get("media_id") and row["media_id"] != f"sha256:{digest}":
                raise ValueError("Invalid image/video identity")
            mime = row.get("mime_type")
            if row["kind"] in {"image", "video"} and (not isinstance(mime, str) or not mime.startswith(row["kind"] + "/")):
                raise ValueError("Invalid image/video media type")
            payload = files.setdefault(name, archive.read(name))
            if len(payload) != row.get("size") or hashlib.sha256(payload).hexdigest() != digest:
                raise ValueError("Conversation resource checksum failed")
        if names != {"manifest.json", "conversation.json", *files}:
            raise ValueError("Conversation archive contains unreferenced entries")
        # A portable archive may not silently depend on the destination's store.
        available = {row.get("media_id") for row in resources if row.get("media_id")}
        media_paths = {kind: {path for row in resources if row["kind"] == kind
                             for path in row["paths"]} for kind in ("image", "video")}
        for message in document["messages"]:
            meta = message.get("metadata") or {}
            for ref in message.get("media_refs") or meta.get("media_refs") or []:
                if isinstance(ref, dict) and ref.get("kind") in {"image", "video"} and ref.get("media_id") not in available:
                    raise ValueError("A referenced image/video is missing from the archive")
            for field, media_kind in (("images", "image"), ("image_files", "image"),
                                      ("videos", "video"), ("video_files", "video")):
                for item in message.get(field) or meta.get(field) or []:
                    raw = item.get("path") if isinstance(item, dict) else item
                    if (isinstance(raw, str) and raw
                            and not raw.startswith(("http://", "https://", "data:"))
                            and raw not in media_paths[media_kind]):
                        raise ValueError("A legacy image/video path is missing from the archive")
        return document, manifest, files
