"""Collect only context media, compression files and Chat handoff materials."""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
import re

from utils.media_store import MediaStore

MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 4096
_MEDIA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RESOURCE = re.compile(r"\.astrion/(?:compact_result|handoff)/[^\s`<>\"'\]\[)（），。；;]+")


def safe_file(root: Path, relative: str) -> Path:
    """Never follow an imported name or existing symlink outside the owned root."""
    from pathlib import PurePosixPath
    name = PurePosixPath(relative)
    if not relative or name.is_absolute() or ".." in name.parts or "\\" in relative:
        raise ValueError("Invalid conversation resource path")
    current = root.resolve()
    for part in name.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Conversation resources cannot use symbolic links")
    current.resolve().relative_to(root.resolve())
    return current


def mapped_path(metadata: dict, project: Path, value: str) -> str:
    """Resolve a historical resource reference without editing its message."""
    aliases = (metadata.get("conversation_transfer") or {}).get("paths") or {}
    relative = aliases.get(value)
    if not isinstance(relative, str):
        return value
    if not relative.startswith(".astrion/conversation_assets/"):
        raise ValueError("Invalid migrated resource mapping")
    return str(safe_file(Path(project), relative))


def tool_path(metadata: dict, project: Path, value: str) -> str:
    mapped = mapped_path(metadata, project, value)
    return Path(mapped).relative_to(project.resolve()).as_posix() if mapped != value else value


def collect(document: dict, data_dir: Path, project: Path | None, *, shared_media=False):
    # Chat has no execution workspace; its imported materials belong to the
    # authenticated account's data directory instead.
    project = (project if project is not None else Path(data_dir)).resolve()
    store = MediaStore(data_dir)
    files: dict[str, bytes] = {}
    resources: list[dict] = []
    seen = set()
    size = 0
    aliases = (document.get("metadata", {}).get("conversation_transfer") or {}).get("paths") or {}

    def add(payload, *, kind, mime=None, media_id=None, paths=()):
        nonlocal size
        digest = hashlib.sha256(payload).hexdigest()
        name = f"assets/{digest}"
        if name not in files:
            size += len(payload)
            if size > MAX_BYTES or len(files) >= MAX_FILES:
                raise ValueError("Conversation resources exceed the 256 MiB export limit")
            files[name] = payload
        key = (kind, media_id, tuple(paths))
        if key not in seen:
            resources.append({"file": name, "sha256": digest, "size": len(payload),
                              "kind": kind, "mime_type": mime, "media_id": media_id,
                              "paths": list(paths)})
            seen.add(key)

    for message in document.get("messages", []):
        meta = message.get("metadata") or {}
        refs = message.get("media_refs") or meta.get("media_refs") or []
        covered_paths = set()
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("kind") not in {"image", "video"}:
                continue
            media_id = str(ref.get("media_id") or "")
            if not _MEDIA.fullmatch(media_id):
                raise ValueError("Invalid image/video identity")
            paths = [p for p in (ref.get("source_path"), ref.get("path")) if isinstance(p, str) and p]
            covered_paths.update(paths)
            if shared_media:
                entry = store.get_media_entry(media_id)
                if entry is None or store._resolve_blob_path({
                        "media_id": media_id, "store_path": entry.get("blob_rel_path")}) is None:
                    raise FileNotFoundError(f"Referenced image/video is missing: {media_id}")
                continue
            payload = store.load_bytes_by_media_id(media_id)
            if payload is None:
                raise FileNotFoundError(f"Referenced image/video is missing: {media_id}")
            add(payload, kind=ref["kind"], mime=ref.get("mime_type"), media_id=media_id, paths=paths)
        for field, kind in (("images", "image"), ("videos", "video"),
                            ("image_files", "image"), ("video_files", "video")):
            for item in message.get(field) or meta.get(field) or []:
                raw = item.get("path") if isinstance(item, dict) else item
                if not isinstance(raw, str) or not raw or raw.startswith(("http://", "https://", "data:")):
                    continue
                # Canonical refs are the actual context bytes; do not reread a
                # subsequently edited or removed original upload.
                if raw in covered_paths or any(raw in row["paths"] for row in resources if row["kind"] == kind):
                    continue
                if project is None:
                    raise FileNotFoundError(f"Image/video has no portable media reference: {raw}")
                actual = mapped_path(document.get("metadata", {}), project, raw)
                candidate = Path(actual)
                if not candidate.is_absolute():
                    candidate = project / candidate
                # Historical paths cannot grant export access outside the project.
                try:
                    relative = candidate.absolute().relative_to(project.resolve()).as_posix()
                except ValueError:
                    raise FileNotFoundError(f"Image/video must be available in its media store: {raw}") from None
                candidate = safe_file(project, relative)
                if not candidate.is_file():
                    raise FileNotFoundError(f"Referenced image/video is missing: {raw}")
                payload = candidate.read_bytes()
                mime = mimetypes.guess_type(raw)[0] or f"{kind}/*"
                add(payload, kind=kind, mime=mime, paths=[raw])

    if project is not None:
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
        records = document.get("metadata", {}).get("deep_compression_records") or []
        referenced = [document.get("messages", []), records]
        logical = {match.rstrip(".,:") for text in strings(referenced)
                   for match in _RESOURCE.findall(text)
                   if len(Path(match).parts) >= 3}
        logical.update(key for key in aliases if _RESOURCE.search(key))
        for raw in sorted(logical):
            if not isinstance(raw, str):
                continue
            matched = _RESOURCE.search(raw)
            if not matched:
                continue
            relative = matched.group().rstrip(".,:")
            if relative.startswith(".astrion/handoff/") and len(Path(relative).parts) < 3:
                continue
            actual = mapped_path(document.get("metadata", {}), project, raw)
            # An absolute historical reference can be resolved through the
            # portable relative name already recorded by a previous import.
            if actual == raw and relative in aliases:
                actual = mapped_path(document.get("metadata", {}), project, relative)
            candidate = safe_file(project, relative) if actual == raw else Path(actual)
            # A handoff reference owns a complete small material directory.
            if relative.startswith(".astrion/handoff/") and actual == raw:
                components = Path(relative).parts[:3]
                directory = safe_file(project, Path(*components).as_posix())
                if not directory.is_dir():
                    raise FileNotFoundError(f"Handoff materials are missing: {relative}")
                candidates = sorted(directory.rglob("*"))
            else:
                candidates = [candidate]
            for path in candidates:
                if path.is_symlink():
                    raise ValueError("Conversation resources cannot use symbolic links")
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise FileNotFoundError(f"Conversation material is missing: {relative}")
                path.resolve().relative_to(project.resolve())
                old = path.relative_to(project).as_posix() if actual == raw else raw
                existing_aliases = [key for key, value in aliases.items()
                                    if project / value == path]
                logical_paths = list(dict.fromkeys([old, *existing_aliases]))
                if actual == raw:
                    logical_paths.append(str(path))
                add(path.read_bytes(), kind="material", paths=logical_paths)
    return resources, files


def restore(resources: list, files: dict[str, bytes], data_dir: Path,
            project: Path | None, conversation_id: str) -> dict[str, str]:
    project = (project if project is not None else Path(data_dir)).resolve()
    store = MediaStore(data_dir)
    paths: dict[str, str] = {}
    for row in resources:
        payload = files[row["file"]]
        kind = row["kind"]
        if kind in {"image", "video"}:
            ref = store.store_bytes(payload, kind=kind, mime_type=row.get("mime_type"))
            if row.get("media_id") and ref["media_id"] != row["media_id"]:
                raise ValueError("Image/video identity does not match its content")
        if project is None:
            continue
        # Use a new namespace. Imported bytes never overwrite project files,
        # summaries or materials from an unrelated conversation.
        extension = mimetypes.guess_extension(row.get("mime_type") or "") or ".bin"
        original = next(iter(row.get("paths") or []), "")
        suffix = Path(original).suffix
        if suffix and re.fullmatch(r"\.[a-zA-Z0-9]{1,12}", suffix):
            extension = suffix
        logical = next((p for p in row.get("paths", [])
                        if p.startswith((".astrion/handoff/", ".astrion/compact_result/"))), None)
        tail = f"materials/{logical}" if kind == "material" and logical else row['sha256'] + extension
        relative = f".astrion/conversation_assets/{conversation_id}/{tail}"
        target = safe_file(project, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != payload:
                raise ValueError("Conflicting conversation resource")
        else:
            with target.open("xb") as stream:
                stream.write(payload)
        for old in row.get("paths") or []:
            paths[old] = relative
            if kind == "material" and logical and old.endswith(logical):
                old_parent, new_parent = Path(old).parent, Path(relative).parent
                while ".astrion/handoff/" in old_parent.as_posix():
                    paths[old_parent.as_posix()] = new_parent.as_posix()
                    old_parent, new_parent = old_parent.parent, new_parent.parent
    return paths
