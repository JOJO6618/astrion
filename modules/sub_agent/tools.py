"""Sub-agent media reading through the execution backend file view.

媒体读取不再直接触碰本地文件系统：
- 元数据（存在性/是否普通文件/后缀）走 `backend.files.inspect_path`，
  它先做 `_validate_path` + `_ensure_host_access` 再 stat，不存在越界 peek；
- 内容走 `backend.files.read_binary`，沿用同一安全读取（no-follow 描述符、
  容器代理、固定 scope 写根等既有语义）。

本地 `exists()`/`resolve()` 一律不在这里使用；容器模式元数据接口未就绪时
fail-closed 到「直接读二进制」，不回退宿主镜像 stat。
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional

from modules.i18n import tr

_METADATA_UNSUPPORTED = "metadata_unsupported_in_container"


def _files_view(backend: Any, file_manager: Any) -> Any:
    """返回后端文件视图；兼容旧的直接传 FileManager 的调用方（只做薄适配）。"""
    if backend is not None:
        return backend.files
    if file_manager is not None:
        from modules.execution_plane.files import LocalFileSystem

        return LocalFileSystem(file_manager)
    return None


async def handle_read_mediafile(
    project_path: Path, arguments: Dict[str, Any], file_manager: Optional[Any] = None,
    *, backend: Optional[Any] = None,
) -> Dict[str, Any]:
    path = arguments.get("path")
    if not path:
        return {"success": False, "error": tr("sub_agent_tools.path_required")}
    try:
        files = _files_view(backend, file_manager)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if files is None:
        return {"success": False, "error": "媒体读取需要可信执行后端（backend.files）。"}

    try:
        info = files.inspect_path(str(path))
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if not isinstance(info, dict) or not info.get("success"):
        error = info.get("error") if isinstance(info, dict) else None
        return {"success": False, "error": error or tr("sub_agent_tools.invalid_path")}

    container_metadata_unsupported = info.get("code") == _METADATA_UNSUPPORTED
    if not container_metadata_unsupported and (
        not info.get("exists") or not info.get("is_file")
    ):
        return {"success": False, "error": tr("sub_agent_tools.file_not_found", path=path)}

    metadata_path = info.get("path") or str(path)
    mime, _ = mimetypes.guess_type(str(metadata_path))
    if not mime or not mime.startswith(("image/", "video/")):
        return {"success": False, "error": tr("sub_agent_tools.forbidden_file_type")}
    try:
        data = files.read_binary(str(path))
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if not isinstance(data, (bytes, bytearray)):
        return {
            "success": False,
            "error": "媒体读取后端返回了非二进制结果，已拒绝。",
        }
    payload = {
        "success": True,
        "path": path,
        "mime": mime,
        "type": "image" if mime.startswith("image/") else "video",
        "b64": base64.b64encode(bytes(data)).decode("utf-8"),
    }
    if info.get("relative_path"):
        payload["relative_path"] = info["relative_path"]
    return payload
