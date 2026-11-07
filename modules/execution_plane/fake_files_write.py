"""FakeFileSystem 的写协议 mixin：写入 / 替换 / 建删 / 改名 / 文件夹。

语义边界与真实 ``FileManager`` 对齐的部分：父目录必须存在、目标已存在时报错、
``create_file`` 无后缀时补 ``file_type``；不模拟 OS 权限与符号链接。
"""
from __future__ import annotations

from typing import Any, Dict, List


class FakeFileWriteMixin:
    """写路径：只改内存字典与内存元数据，不触发任何真实副作用。"""

    # ------------------------------------------------------------------
    # 写入与结构变更
    # ------------------------------------------------------------------
    def write_file(self, path: str, content: str, mode: str = "w") -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if not isinstance(content, str):
            return {"success": False, "error": "content must be text"}
        if key == "" or self._is_dir(key):
            return {"success": False, "error": "target is a directory"}
        parent = key.rsplit("/", 1)[0] if "/" in key else ""
        if parent and not self._is_dir(parent):
            return {"success": False, "error": "parent directory not found"}
        original = self._text(key) if self._is_file(key) else None
        if self._is_file(key) and original is None and mode != "wb":
            return {"success": False, "error": "file is not valid UTF-8 text"}
        payload = content.encode("utf-8")
        if mode == "a" and self._is_file(key):
            data = dict.__getitem__(self, key) + payload
        elif mode == "a":
            data = payload
        else:
            data = payload
        existed = self._is_file(key)
        dict.__setitem__(self, key, data)
        self._meta.setdefault(key, {"created": self._now(), "modified": self._now()})
        self._touch(key)
        return {
            "success": True,
            "path": key,
            "action": "updated" if existed else "created",
            "original_file": original,
            "new_file": content if mode != "a" else (original or "") + content,
        }

    def write_binary(self, path: str, content: bytes) -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if not isinstance(content, (bytes, bytearray, memoryview)):
            return {"success": False, "error": "content must be bytes"}
        if key == "" or self._is_dir(key):
            return {"success": False, "error": "target is a directory"}
        parent = key.rsplit("/", 1)[0] if "/" in key else ""
        if parent and not self._is_dir(parent):
            return {"success": False, "error": "parent directory not found"}
        dict.__setitem__(self, key, bytes(content))
        self._meta.setdefault(key, {"created": self._now(), "modified": self._now()})
        self._touch(key)
        return {"success": True, "path": key, "size": len(content)}

    def replace_many_in_file(
        self, path: str, replacements: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if not self._is_file(key):
            return {"success": False, "error": "file not found"}
        original = self._text(key)
        if original is None:
            return {"success": False, "error": "file is not valid UTF-8 text"}
        new_content = original
        applied = 0
        for rep in replacements or []:
            if not isinstance(rep, dict):
                return {"success": False, "error": "replacements must be objects"}
            old = rep.get("old_string", "")
            new = rep.get("new_string", "")
            if not old or old not in new_content:
                continue
            if rep.get("replace_all"):
                applied += new_content.count(old)
                new_content = new_content.replace(old, new)
            else:
                applied += 1
                new_content = new_content.replace(old, new, 1)
        if applied == 0:
            return {"success": False, "error": "no matching content found"}
        dict.__setitem__(self, key, new_content.encode("utf-8"))
        self._touch(key)
        return {
            "success": True,
            "path": key,
            "original_file": original,
            "new_file": new_content,
            "replacements_applied": applied,
        }

    def create_file(self, path: str, content: str = "", file_type: str = "txt") -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if key == "":
            return {"success": False, "error": "path is required"}
        if not key.rsplit("/", 1)[-1].count("."):
            suffix = f".{file_type.lstrip('.')}" if file_type else ""
            if suffix:
                key = key + suffix
        if self._is_file(key) or self._is_dir(key):
            return {"success": False, "error": "file already exists"}
        parent = key.rsplit("/", 1)[0] if "/" in key else ""
        if parent and not self._is_dir(parent):
            return {"success": False, "error": "parent directory not found"}
        now = self._now()
        dict.__setitem__(self, key, content.encode("utf-8"))
        self._meta[key] = {"created": now, "modified": now}
        return {"success": True, "path": key, "action": "created"}

    def delete_file(self, path: str) -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if not self._is_file(key):
            return {"success": False, "error": "file not found"}
        dict.__delitem__(self, key)
        self._meta.pop(key, None)
        return {"success": True, "path": key, "action": "deleted"}

    def rename_file(self, old_path: str, new_path: str) -> Dict[str, Any]:
        old_key, error = self._normalize(old_path)
        if error:
            return {"success": False, "error": error}
        new_key, error = self._normalize(new_path)
        if error:
            return {"success": False, "error": error}
        if not self._is_file(old_key):
            return {"success": False, "error": "original file not found"}
        if self._is_file(new_key) or self._is_dir(new_key):
            return {"success": False, "error": "target exists"}
        parent = new_key.rsplit("/", 1)[0] if "/" in new_key else ""
        if parent and not self._is_dir(parent):
            return {"success": False, "error": "parent directory not found"}
        data = dict.__getitem__(self, old_key)
        dict.__delitem__(self, old_key)
        dict.__setitem__(self, new_key, data)
        record = self._meta.pop(old_key, {"created": self._now(), "modified": self._now()})
        record["modified"] = self._now()
        self._meta[new_key] = record
        return {
            "success": True,
            "old_path": old_key,
            "new_path": new_key,
            "action": "renamed",
        }

    def create_folder(self, path: str) -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if key == "":
            return {"success": False, "error": "folder already exists"}
        if self._is_file(key) or self._is_dir(key):
            return {"success": False, "error": "folder already exists"}
        parent = key.rsplit("/", 1)[0] if "/" in key else ""
        if parent and not self._is_dir(parent):
            return {"success": False, "error": "parent folder not found"}
        self._dirs.add(key)
        now = self._now()
        self._meta[key] = {"created": now, "modified": now}
        return {"success": True, "path": key, "action": "created"}

    def delete_folder(self, path: str) -> Dict[str, Any]:
        key, error = self._normalize(path)
        if error:
            return {"success": False, "error": error}
        if key == "":
            return {"success": False, "error": "workspace root cannot be deleted"}
        if not self._is_dir(key):
            return {"success": False, "error": "directory not found"}
        prefix = key + "/"
        for file_key in [k for k in dict.keys(self) if k == key or k.startswith(prefix)]:
            dict.__delitem__(self, file_key)
            self._meta.pop(file_key, None)
        self._dirs = {d for d in self._dirs if d != key and not d.startswith(prefix)}
        self._meta.pop(key, None)
        return {"success": True, "path": key, "action": "deleted"}



__all__ = ["FakeFileWriteMixin"]
