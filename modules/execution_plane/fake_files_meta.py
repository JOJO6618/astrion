"""FakeFileSystem 的元数据 mixin：列表 / 信息 / 检查 / 解析路径。

关键约束：元数据只从内存记录推导（size 来自 bytes 长度，时间来自注入时钟），
**绝不**调用本地 ``Path.exists/resolve/stat``——否则"执行侧元数据"会退回宿主事实。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


class FakeFileMetaMixin:
    """元数据视图：授权 + 存在性 + 尺寸/时间/后缀都来自内存。"""

    # ------------------------------------------------------------------
    # 元数据（只读内存，绝不 stat 本地磁盘）
    # ------------------------------------------------------------------
    def list_files(self, path: str = "") -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if not self._is_dir(key):
            return {"success": False, "error": "directory not found"}
        prefix = key + "/" if key else ""
        files: List[Dict[str, Any]] = []
        folders: List[Dict[str, Any]] = []
        seen_folders: set = set()
        for file_key in dict.keys(self):
            if not file_key.startswith(prefix):
                continue
            rest = file_key[len(prefix):]
            if "/" in rest:
                folder_name = rest.split("/", 1)[0]
                if folder_name.startswith(".") or folder_name in seen_folders:
                    continue
                seen_folders.add(folder_name)
                folders.append({
                    "name": folder_name,
                    "path": f"{prefix}{folder_name}",
                })
                continue
            if rest.startswith("."):
                continue
            record = self._meta_for(file_key)
            files.append({
                "name": rest,
                "path": file_key,
                "size": len(dict.__getitem__(self, file_key)),
                "modified": datetime.fromtimestamp(record["modified"]).isoformat(),
            })
        for dir_key in self._dirs:
            if not dir_key.startswith(prefix) or dir_key == key:
                continue
            rest = dir_key[len(prefix):]
            if "/" in rest or rest.startswith(".") or rest in seen_folders:
                continue
            seen_folders.add(rest)
            folders.append({"name": rest, "path": dir_key})
        return {
            "success": True,
            "path": key if key else ".",
            "files": sorted(files, key=lambda item: item["name"]),
            "folders": sorted(folders, key=lambda item: item["name"]),
        }

    def get_file_info(self, path: str) -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        is_file = self._is_file(key)
        if not is_file and not self._is_dir(key):
            return {"success": False, "error": "path not found"}
        record = self._meta_for(key)
        name = key.rsplit("/", 1)[-1] if key else self.workspace_root
        suffix = ("." + name.rsplit(".", 1)[1]) if (is_file and "." in name) else None
        return {
            "success": True,
            "path": key,
            "name": name,
            "type": "file" if is_file else "folder",
            "size": len(dict.__getitem__(self, key)) if is_file else 0,
            "created": datetime.fromtimestamp(record["created"]).isoformat(),
            "modified": datetime.fromtimestamp(record["modified"]).isoformat(),
            "extension": suffix,
        }

    def inspect_path(self, path: str, access: str = "read") -> Dict[str, Any]:
        normalized_access = (access or "").strip().lower()
        if normalized_access not in ("read", "write"):
            return {"success": False, "error": "access must be 'read' or 'write'"}
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        is_file = self._is_file(key)
        is_dir = self._is_dir(key)
        name = key.rsplit("/", 1)[-1] if key else ""
        suffix = ("." + name.rsplit(".", 1)[1]) if (is_file and "." in name) else ""
        record = self._meta_for(key) if (is_file or is_dir) else None
        return {
            "success": True,
            "path": key,
            "relative_path": key,
            "exists": bool(is_file or is_dir),
            "is_file": is_file,
            "is_dir": is_dir,
            "is_symlink": False,
            "within_workspace": True,
            "suffix": suffix,
            "size": len(dict.__getitem__(self, key)) if is_file else None,
            "mtime": record["modified"] if record else None,
        }

    def resolve_path(self, path: str, access: str = "read") -> Dict[str, Any]:
        normalized_access = (access or "").strip().lower()
        if normalized_access not in ("read", "write"):
            return {"success": False, "error": "access must be 'read' or 'write'"}
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        return {
            "success": True,
            "path": key,
            "relative_path": key,
            "access": normalized_access,
            "within_workspace": True,
        }



__all__ = ["FakeFileMetaMixin"]
