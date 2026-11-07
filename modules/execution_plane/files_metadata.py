"""公共文件执行层：元数据 mixin（授权先于 stat / 容器 policy-checked 动作）。

该 mixin 只负责 ``list_files/get_file_info/inspect_path/resolve_path`` 及其
路径换算 helper；内容读写仍由 ``files.LocalFileSystem`` 显式转发 FileManager。
宿主模式复用 ``_validate_path`` + ``_ensure_host_access``；容器模式经
``_container_call`` 调用容器脚本，失败原样返回，不回退宿主镜像。
"""
from __future__ import annotations

import os
import stat as stat_module
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from modules.i18n import tr

_VALID_ACCESS = {"read", "write"}


class MetadataFileSystemMixin:
    """``LocalFileSystem`` 的元数据能力；依赖宿主对象的 ``_file_manager``。"""

    _file_manager: Any

    # ------------------------------------------------------------------
    # 元数据：授权先于 stat
    # ------------------------------------------------------------------
    def list_files(self, path: str = "") -> Dict[str, Any]:
        """列出目录内容；容器模式走容器内 policy-checked list_files。"""
        if self._is_container_mode():
            return self._call_container(
                "list_files", {"path": self._container_relative(path)}
            )
        # FileManager.list_files 先 _validate_path + _ensure_host_access 再 iterdir，
        # 每个子项也先授权再 stat，顺序满足“不允许 peek 越界”。
        return self._file_manager.list_files(path)

    def get_file_info(self, path: str) -> Dict[str, Any]:
        """旧结构兼容（ISO 时间字符串）；宿主侧保证授权先于 exists/stat。"""
        if self._is_container_mode():
            return self._call_container(
                "get_file_info", {"path": self._container_relative(path)}
            )
        fm = self._file_manager
        valid, error, full_path = fm._validate_path(path)
        if not valid or full_path is None:
            return {
                "success": False,
                "error": error or tr("file_manager.path_outside_project"),
            }
        ok, message = fm._ensure_host_access(full_path, "read")
        if not ok:
            return {"success": False, "error": message}

        try:
            if not full_path.exists():
                return {"success": False, "error": tr("file_manager.file_not_found")}
            info = full_path.stat()
            is_file = full_path.is_file()
            within, relative = self._relative_info(full_path)
            return {
                "success": True,
                "path": relative if within else str(full_path),
                "name": full_path.name,
                "type": "file" if is_file else "folder",
                "size": int(info.st_size),
                "created": datetime.fromtimestamp(info.st_ctime).isoformat(),
                "modified": datetime.fromtimestamp(info.st_mtime).isoformat(),
                "extension": full_path.suffix if is_file else None,
            }
        except OSError as exc:
            return {"success": False, "error": str(exc)}

    def inspect_path(self, path: str, access: str = "read") -> Dict[str, Any]:
        """授权后检查路径；不存在返回 success=True + exists=False。

        返回字段：success,path,relative_path,exists,is_file,is_dir,is_symlink,
        within_workspace,suffix,size,mtime(epoch 秒)；拒绝时只有 success/error。
        """
        normalized_access = (access or "").strip().lower()
        if normalized_access not in _VALID_ACCESS:
            return {"success": False, "error": "access 仅支持 'read' 或 'write'"}
        if self._is_container_mode():
            return self._call_container(
                "inspect_path",
                {
                    "path": self._container_relative(path),
                    "access": normalized_access,
                },
            )

        fm = self._file_manager
        valid, error, full_path = fm._validate_path(path)
        if not valid or full_path is None:
            return {
                "success": False,
                "error": error or tr("file_manager.path_outside_project"),
            }
        ok, message = fm._ensure_host_access(full_path, normalized_access)
        if not ok:
            return {"success": False, "error": message}

        within_workspace, relative_path = self._relative_info(full_path)
        candidate = self._candidate_path(path)
        try:
            link_info = os.lstat(candidate)
            is_symlink = stat_module.S_ISLNK(link_info.st_mode)
        except (OSError, ValueError):
            link_info = None
            is_symlink = False

        try:
            exists = full_path.exists()
            is_file = bool(exists and full_path.is_file())
            is_dir = bool(exists and full_path.is_dir())
        except OSError:
            exists = is_file = is_dir = False

        size: Optional[int] = None
        mtime: Optional[float] = None
        try:
            if exists:
                info = full_path.stat()
                mtime = float(info.st_mtime)
                if is_file:
                    size = int(info.st_size)
            elif link_info is not None:
                # 悬空符号链接：目标不存在，但链接本身有 mtime。
                mtime = float(link_info.st_mtime)
        except OSError:
            pass

        return {
            "success": True,
            "path": relative_path if within_workspace else str(full_path),
            "relative_path": relative_path if within_workspace else None,
            "exists": bool(exists),
            "is_file": bool(is_file),
            "is_dir": bool(is_dir),
            "is_symlink": bool(is_symlink),
            "within_workspace": bool(within_workspace),
            "suffix": candidate.suffix,
            "size": size,
            "mtime": mtime,
        }

    def resolve_path(self, path: str, access: str = "read") -> Dict[str, Any]:
        """只做路径校验 + 访问授权，不 stat、不判断存在性。"""
        normalized_access = (access or "").strip().lower()
        if normalized_access not in _VALID_ACCESS:
            return {"success": False, "error": "access 仅支持 'read' 或 'write'"}
        if self._is_container_mode():
            return self._call_container(
                "resolve_path",
                {
                    "path": self._container_relative(path),
                    "access": normalized_access,
                },
            )
        fm = self._file_manager
        valid, error, full_path = fm._validate_path(path)
        if not valid or full_path is None:
            return {
                "success": False,
                "error": error or tr("file_manager.path_outside_project"),
            }
        ok, message = fm._ensure_host_access(full_path, normalized_access)
        if not ok:
            return {"success": False, "error": message}
        within_workspace, relative_path = self._relative_info(full_path)
        return {
            "success": True,
            "path": relative_path if within_workspace else str(full_path),
            "relative_path": relative_path if within_workspace else None,
            "access": normalized_access,
            "within_workspace": bool(within_workspace),
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _is_container_mode(self) -> bool:
        """容器语义优先：docker 模式一律走容器代理，不静默回退宿主 stat。"""
        return bool(self._file_manager._is_docker_mode())

    def _call_container(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """调用容器代理动作；失败原样返回，绝不回退宿主实现。"""
        try:
            result = self._file_manager._container_call(action, payload)
        except Exception as exc:  # 工具箱未就绪/权限/子进程异常等
            return {"success": False, "error": str(exc)}
        if not isinstance(result, dict):
            return {
                "success": False,
                "error": f"容器文件动作 {action} 返回了非 dict 结果",
            }
        return result

    def _container_relative(self, path: str) -> str:
        """把宿主侧路径转换为容器根相对路径（纯字面转换，不 stat）。

        相对路径先落到 ``_base_dir()``（子智能体固定工作目录 / 工作区根），
        再相对项目根换算；否则 Docker 子智能体会把 ``foo.txt`` 误当成
        ``/workspace/foo.txt``，而实际读写落在 ``/workspace/agent_x/foo.txt``。
        """
        text = (path or "").strip()
        if not text:
            text = "."
        if text == "/workspace":
            return "."
        if text.startswith("/workspace/"):
            return text[len("/workspace/"):]
        looks_absolute = (
            text.startswith("/")
            or text.startswith("\\")
            or (len(text) > 1 and text[1] == ":")
        )
        root = str(getattr(self._file_manager, "project_path", "") or "")
        if not root:
            return text
        candidate = Path(text) if looks_absolute else (self._base_dir() / text)
        try:
            return Path(os.path.relpath(str(candidate), root)).as_posix()
        except (OSError, ValueError):
            return text

    def _workspace_root(self) -> Path:
        scope = _current_scope()
        if scope is not None:
            try:
                return Path(_fixed_workspace_root(scope)).resolve()
            except Exception:
                pass
        return Path(self._file_manager.project_path).resolve()

    def _base_dir(self) -> Path:
        scope = _current_scope()
        if scope is not None and getattr(scope, "is_sub_agent", False):
            try:
                return Path(_fixed_working_dir(scope))
            except Exception:
                pass
        return self._workspace_root()

    def _relative_info(self, full_path: Path) -> Tuple[bool, Optional[str]]:
        try:
            relative = full_path.relative_to(self._workspace_root())
        except ValueError:
            return False, None
        return True, str(relative)

    def _candidate_path(self, raw: str) -> Path:
        """还原“未 resolve”的目标路径，仅用于 lstat/is_symlink。

        授权始终基于 ``_validate_path`` 返回的 resolved 路径；这里不参与
        权限判断，只负责在链接未被跟随前拿到链接自身元数据。
        """
        text = (raw or "").strip()
        root = self._workspace_root()
        base = self._base_dir()
        if text == "/workspace":
            return root
        if text.startswith("/workspace/"):
            return root / text.split("/workspace/", 1)[1]
        if not text:
            return base
        looks_absolute = (
            text.startswith("/")
            or text.startswith("\\")
            or (len(text) > 1 and text[1] == ":")
        )
        candidate = Path(text).expanduser()
        if looks_absolute:
            return candidate
        return base / text


# 惰性包装：避免 execution_plane 在导入期就拉起 execution_scope/scoped_policy。
def _current_scope():
    from modules.execution_scope import current_execution_scope

    return current_execution_scope()


def _fixed_workspace_root(scope) -> Path:
    from modules.scoped_execution_policy import fixed_workspace_root

    return Path(fixed_workspace_root(scope))


def _fixed_working_dir(scope) -> Path:
    from modules.scoped_execution_policy import fixed_working_dir

    return Path(fixed_working_dir(scope))


__all__ = ["MetadataFileSystemMixin"]
