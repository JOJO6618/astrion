# modules/file_manager.py - 文件管理模块（添加行编辑功能）

import os
import shutil
from pathlib import Path
import re
from bisect import bisect_right
from typing import Any, Optional, Dict, List, Set, Tuple, TYPE_CHECKING
from datetime import datetime
try:
    from config import (
        MAX_FILE_SIZE,
        FORBIDDEN_PATHS,
        FORBIDDEN_ROOT_PATHS,
        OUTPUT_FORMATS,
        READ_TOOL_MAX_FILE_SIZE,
        PROJECT_MAX_STORAGE_BYTES,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
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
        PROJECT_MAX_STORAGE_BYTES,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
    )
from modules.container_file_proxy import ContainerFileProxy
from modules.host_sandbox_policy import get_macos_writable_paths, get_macos_readable_paths
from utils.logger import setup_logger
from modules.i18n import tr

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle

# 临时禁用长度检查
DISABLE_LENGTH_CHECK = True

logger = setup_logger(__name__)

class PathMixin:
    """FileManager path mixin 能力 mixin。"""

    def _get_project_size(self) -> int:
        """计算项目目录的总大小（字节），遇到异常时记录并抛出。"""
        if not self._is_docker_mode():
            return 0
        total = 0
        if not self.project_path.exists():
            return 0

        for path in self.project_path.rglob('*'):
            if not path.is_file():
                continue
            try:
                total += path.stat().st_size
            except Exception as exc:
                logger.error(
                    "Failed to stat %s while calculating project size: %s",
                    path,
                    exc,
                    exc_info=True,
                )
                raise
        return total

    def _validate_path(self, path: str) -> Tuple[bool, str, Path]:
        """
        验证路径安全性
        
        Returns:
            (是否有效, 错误信息, 完整路径)
        """
        original_path = path
        project_root = Path(self.project_path).resolve()
        if project_root != self.project_path:
            self.project_path = project_root

        if self._is_host_mode():
            normalized = (path or "").strip()
            if normalized == "/workspace":
                normalized = ""
            elif normalized.startswith("/workspace/"):
                normalized = normalized.split("/workspace/", 1)[1]
            if not normalized:
                return True, "", project_root
            if Path(normalized).is_absolute() or (len(normalized) > 1 and normalized[1] == ":"):
                full_path = Path(normalized).expanduser().resolve()
            else:
                full_path = (project_root / normalized).resolve()
            return True, "", full_path
        
        # 不允许绝对路径（除非是在项目内的绝对路径）
        if path.startswith('/') or path.startswith('\\') or (len(path) > 1 and path[1] == ':'):
            # 如果是绝对路径，检查是否指向项目内
            try:
                test_path = Path(path).resolve()
                test_path.relative_to(project_root)
                # 如果成功，说明绝对路径在项目内，转换为相对路径
                path = str(test_path.relative_to(project_root))
            except ValueError:
                if str(original_path).replace("\\", "/").startswith("/workspace"):
                    return False, tr("file_manager.path_outside_workspace_detailed"), None
                return False, tr("file_manager.path_outside_project"), None
        
        # 检查是否包含向上遍历
        if ".." in path:
            return False, tr("file_manager.path_traversal_blocked"), None
        
        # 构建完整路径
        full_path = (project_root / path).resolve()
        
        # 检查是否在项目目录内
        try:
            full_path.relative_to(project_root)
        except ValueError:
            return False, tr("file_manager.path_outside_project"), None
        
        # 检查禁止的路径
        path_str = str(full_path)
        
        for forbidden_root in FORBIDDEN_ROOT_PATHS:
            if path_str == forbidden_root:
                return False, tr("file_manager.path_forbidden_root", path=forbidden_root), None
        
        for forbidden in FORBIDDEN_PATHS:
            if path_str.startswith(forbidden + os.sep) or path_str == forbidden:
                return False, tr("file_manager.path_forbidden_system", path=forbidden), None
        
        return True, "", full_path

    def _relative_path(self, full_path: Path) -> str:
        try:
            return str(full_path.relative_to(self.project_path))
        except ValueError:
            return str(full_path)

    @staticmethod
    def _path_in_allowed_roots(target: Path, roots: List[Path]) -> bool:
        for root in roots:
            try:
                target.relative_to(root)
                return True
            except Exception:
                continue
        return False

    def _host_allowed_roots(self, access: str) -> List[Path]:
        # 临时目录白名单按平台分流：POSIX 用 /tmp、/private/tmp；
        # Windows 用系统临时目录（Path("/tmp") 在 Windows 会解析为 C:\tmp，语义错误）
        if os.name == "nt":
            import tempfile
            temp_roots = [Path(tempfile.gettempdir()).resolve()]
        else:
            temp_roots = [Path("/tmp").resolve(), Path("/private/tmp").resolve()]
        roots: List[Path] = [self.project_path.resolve(), *temp_roots]
        raw_items = get_macos_writable_paths() if access == "write" else get_macos_readable_paths()
        for raw in raw_items:
            try:
                p = Path(raw).expanduser().resolve()
            except Exception:
                continue
            if p not in roots:
                roots.append(p)
        return roots

    def _ensure_host_access(self, full_path: Path, access: str) -> Tuple[bool, str]:
        if not self._is_host_mode():
            return True, ""
        # 执行环境为 direct（完全访问权限）时，run_command 不套沙箱、可读写任意路径；
        # read_file/write_file/edit_file 为进程内文件操作，本就不走 OS 沙箱，此处与
        # run_command 语义对齐，直接放行。sandbox 模式下保持授权范围检查不变。
        if getattr(self, "host_execution_mode", "sandbox") == "direct":
            return True, ""
        check_target = full_path
        if access == "write" and not full_path.exists():
            check_target = full_path.parent.resolve()
        allowed_roots = self._host_allowed_roots(access)
        if self._path_in_allowed_roots(check_target.resolve(), allowed_roots):
            return True, ""
        if access == "write":
            return False, tr("file_manager.host_access_write_denied")
        return False, tr("file_manager.host_access_read_denied")
