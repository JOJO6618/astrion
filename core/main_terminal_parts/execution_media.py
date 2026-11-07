"""Validate tool media through the selected executor, never the controller disk."""
from __future__ import annotations

from modules.execution_plane import get_execution_backend
from modules.i18n import tr


def prepare_media_view(terminal, path: str, kind: str) -> dict:
    if not path:
        return {"success": False, "error": tr("tools_exec.path_empty")}
    files = get_execution_backend(terminal).files
    info = files.inspect_path(path)
    if not info.get("success"):
        return {"success": False, "error": info.get("error") or "Unable to inspect media"}
    if not info.get("exists") or not info.get("is_file"):
        return {"success": False, "error": tr(f"tools_exec.{kind}_not_found", path=path)}
    extension = str(info.get("suffix") or "").lower()
    extensions = {
        "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"},
        "video": {".mp4", ".mov", ".mkv", ".avi", ".webm"},
    }
    if extension not in extensions[kind]:
        return {"success": False, "error": tr(f"tools_exec.{kind}_unsupported_format", suffix=extension)}
    size = info.get("size")
    limit = (10 if kind == "image" else 50) * 1024 * 1024
    if not isinstance(size, int) or size > limit:
        return {"success": False, "error": tr(f"tools_exec.{kind}_too_large")}
    setattr(terminal, f"pending_{kind}_view", {"path": path})
    result = {"success": True, "message": tr(f"tools_exec.{kind}_attached"), "path": path}
    if kind == "image":
        result["size"] = size
    return result
