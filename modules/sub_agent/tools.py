"""子智能体特有工具实现（read_mediafile）。

read_mediafile 直接读取项目内媒体文件并返回 base64。
（原 search_workspace 工具已移除：子智能体需要搜索时改用 run_command
执行 rg / grep / find，走主进程沙箱链路，避免进程内遍历大目录导致内存失控。）
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict

from modules.i18n import tr


async def handle_read_mediafile(project_path: Path, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """read_mediafile 实现：直接读取项目内媒体文件并返回 base64。"""
    path = arguments.get("path")
    if not path:
        return {"success": False, "error": tr("sub_agent_tools.path_required")}
    try:
        abs_path = (project_path / path).resolve()
        abs_path.relative_to(project_path)
    except Exception:
        return {"success": False, "error": tr("sub_agent_tools.invalid_path")}

    if not abs_path.exists() or not abs_path.is_file():
        return {"success": False, "error": tr("sub_agent_tools.file_not_found", path=path)}

    mime, _ = mimetypes.guess_type(str(abs_path))
    if not mime or (not mime.startswith("image/") and not mime.startswith("video/")):
        return {"success": False, "error": tr("sub_agent_tools.forbidden_file_type")}

    try:
        data = abs_path.read_bytes()
        b64 = base64.b64encode(data).decode("utf-8")
        file_type = "image" if mime.startswith("image/") else "video"
        return {"success": True, "path": path, "mime": mime, "type": file_type, "b64": b64}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
