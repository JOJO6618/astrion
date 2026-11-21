# modules/file_manager.py - 文件管理模块（添加行编辑功能）

import os
import shutil
import stat
from pathlib import Path
import re
from bisect import bisect_right
from typing import Any, Optional, Dict, List, Set, Tuple
from datetime import datetime
try:
    from config import (
        MAX_FILE_SIZE,
        FORBIDDEN_PATHS,
        FORBIDDEN_ROOT_PATHS,
        OUTPUT_FORMATS,
        READ_TOOL_MAX_FILE_SIZE,
    )
except ImportError:  # 兼容全局环境中存在同名包的情况
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        MAX_FILE_SIZE,
        FORBIDDEN_PATHS,
        FORBIDDEN_ROOT_PATHS,
        OUTPUT_FORMATS,
        READ_TOOL_MAX_FILE_SIZE,
    )
from modules.host_sandbox_policy import get_macos_writable_paths, get_macos_readable_paths
from utils.logger import setup_logger
from modules.i18n import tr
from . import scoped_io

# 临时禁用长度检查
DISABLE_LENGTH_CHECK = True

logger = setup_logger(__name__)

class FileManagerBase:
    """FileManager 基础类：初始化与 Host 原生文件访问。"""

    def __init__(self, project_path: str, container_session: Any = None, data_dir: Optional[str] = None):
        self.project_path = Path(project_path).resolve()
        # 调用签名暂保留 container_session（后续统一移除）。主仓文件执行只有 Host
        # 原生路径：Docker 容器会话必须显式拒绝，绝不静默转成 Host 文件系统。
        self.container_session: Any = None
        self._data_dir: Optional[str] = data_dir
        # 宿主机执行环境（sandbox / direct），由主终端同步；
        # direct 时与 run_command 对齐：不走路径授权检查（不套沙箱语义）。
        self.host_execution_mode: str = "sandbox"
        self.set_container_session(container_session)
        if data_dir:
            from modules.workspace_memory import register_host_memory
            register_host_memory(str(self.project_path), str(data_dir))

    def set_host_execution_mode(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        self.host_execution_mode = "direct" if normalized == "direct" else "sandbox"

    def _load_personalization_config(self) -> Optional[Dict]:
        """加载个性化配置"""
        if not self._data_dir:
            return None
        try:
            from modules.personalization_manager import load_personalization_config
            return load_personalization_config(self._data_dir)
        except Exception:
            return None

    def set_container_session(self, container_session: Any):
        """保留旧调用签名；Docker 会话明确拒绝，非 Docker 句柄不参与文件访问。"""
        if container_session is None:
            self.container_session = None
            return
        if getattr(container_session, "mode", None) == "docker":
            raise ValueError(
                "Docker 容器文件代理已移出主仓执行链；拒绝容器会话，也不会回退 Host 文件系统。"
            )
        # 旧 Host 句柄只记录，不参与任何路径解析或读写决策。
        self.container_session = container_session

    def _native_open(self, path, mode="r", encoding="utf-8"):
        access = "read" if mode in {"r", "rb"} else "write"
        ok, error = self._ensure_host_access(Path(path), access)
        if not ok:
            raise PermissionError(error)
        if scoped_io.sandbox_scope():
            if mode == "wb":
                return self._native_open_binary_write(path)
            create_roots = self._host_allowed_roots("write") if access == "write" else None
            return scoped_io.open_checked(path, mode, encoding, create_roots=create_roots)
        return open(path, mode, **({} if "b" in mode else {"encoding": encoding}))

    def _native_open_binary_write(self, path):
        """作用域内二进制写：目录句柄 + O_NOFOLLOW，先校验再截断。"""
        with scoped_io.checked_parent(
            path,
            create=True,
            create_roots=self._host_allowed_roots("write"),
        ) as (parent_fd, name):
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_NOFOLLOW
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            fd = os.open(name, flags, 0o666, dir_fd=parent_fd)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise PermissionError(
                        "Scoped binary write refuses special files and multiply linked files"
                    )
                os.ftruncate(fd, 0)
                return os.fdopen(fd, "wb")
            except BaseException:
                os.close(fd)
                raise

    def write_binary(self, path: str, content: bytes) -> Dict:
        """精确写入二进制内容；仅接受 bytes-like，绝不文本编码/解码回退。"""
        if not isinstance(content, (bytes, bytearray, memoryview)):
            return {
                "success": False,
                "error": "write_binary 仅接受 bytes，不做文本编码回退",
            }
        data = bytes(content)
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        ok, msg = self._ensure_host_access(full_path, "write")
        if not ok:
            return {"success": False, "error": msg}
        try:
            relative_path = self._relative_path(full_path)
            self._native_mkdir(full_path.parent)
            with self._native_open(full_path, "wb") as stream:
                stream.write(data)
            return {
                "success": True,
                "path": relative_path,
                "size": len(data),
                "mode": "wb",
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def read_binary(self, path):
        with self._native_open(path, "rb") as stream:
            return stream.read()

    def _native_read_text(self, path):
        with self._native_open(path) as stream:
            return stream.read()

    def _native_mkdir(self, path):
        ok, error = self._ensure_host_access(Path(path), "write")
        if not ok:
            raise PermissionError(error)
        if scoped_io.sandbox_scope():
            scoped_io.mkdir_checked(path, create_roots=self._host_allowed_roots("write"))
        else:
            Path(path).mkdir(parents=True, exist_ok=True)

    def _native_unlink(self, path):
        ok, error = self._ensure_host_access(Path(path), "write")
        if not ok:
            raise PermissionError(error)
        if scoped_io.sandbox_scope():
            scoped_io.unlink_checked(path)
        else:
            Path(path).unlink()

    def _native_rename(self, source, target):
        for path in (source, target):
            ok, error = self._ensure_host_access(Path(path), "write")
            if not ok:
                raise PermissionError(error)
        if scoped_io.sandbox_scope():
            scoped_io.rename_checked(source, target)
        else:
            Path(source).rename(target)

    def _native_rmtree(self, path):
        ok, error = self._ensure_host_access(Path(path), "write")
        if not ok:
            raise PermissionError(error)
        if scoped_io.sandbox_scope():
            scoped_io.rmtree_checked(path)
        else:
            shutil.rmtree(path)
