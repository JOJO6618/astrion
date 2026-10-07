"""Sub-agent media reading through its trusted file access policy."""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional

from modules.i18n import tr


async def handle_read_mediafile(
    project_path: Path, arguments: Dict[str, Any], file_manager: Optional[Any] = None,
) -> Dict[str, Any]:
    path = arguments.get("path")
    if not path:
        return {"success": False, "error": tr("sub_agent_tools.path_required")}
    try:
        if file_manager is None:
            abs_path = (project_path / path).resolve()
            abs_path.relative_to(project_path.resolve())
        else:
            valid, error, abs_path = file_manager._validate_path(str(path))
            if not valid:
                return {"success": False, "error": error}
            allowed, error = file_manager._ensure_host_access(abs_path, "read")
            if not allowed:
                return {"success": False, "error": error}
    except Exception:
        return {"success": False, "error": tr("sub_agent_tools.invalid_path")}

    if not (file_manager and file_manager._use_container()) and (not abs_path.exists() or not abs_path.is_file()):
        return {"success": False, "error": tr("sub_agent_tools.file_not_found", path=path)}
    mime, _ = mimetypes.guess_type(str(abs_path))
    if not mime or not mime.startswith(("image/", "video/")):
        return {"success": False, "error": tr("sub_agent_tools.forbidden_file_type")}
    try:
        if file_manager is None:
            raise PermissionError("媒体读取需要可信文件管理器。")
        data = file_manager.read_binary(abs_path)
        return {
            "success": True, "path": path, "mime": mime,
            "type": "image" if mime.startswith("image/") else "video",
            "b64": base64.b64encode(data).decode("utf-8"),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
