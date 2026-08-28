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

class FileManagerBase:
    """FileManager 基础类，包含初始化与容器访问。"""

    def __init__(self, project_path: str, container_session: Optional["ContainerHandle"] = None, data_dir: Optional[str] = None):
        self.project_path = Path(project_path).resolve()
        self.container_session: Optional["ContainerHandle"] = None
        self._container_proxy: Optional[ContainerFileProxy] = None
        self._data_dir: Optional[str] = data_dir
        # 宿主机执行环境（sandbox / direct），由主终端同步；
        # direct 时与 run_command 对齐：不走路径授权检查（不套沙箱语义）。
        self.host_execution_mode: str = "sandbox"
        self.set_container_session(container_session)

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

    def set_container_session(self, container_session: Optional["ContainerHandle"]):
        self.container_session = container_session
        if (
            container_session
            and container_session.mode == "docker"
            and container_session.container_name
        ):
            if self._container_proxy is None:
                self._container_proxy = ContainerFileProxy(container_session)
            else:
                self._container_proxy.update_session(container_session)
        else:
            self._container_proxy = None

    def _use_container(self) -> bool:
        return self._container_proxy is not None and self._container_proxy.is_available()

    def _container_call(self, action: str, payload: Dict) -> Dict:
        if not self._use_container():
            return {
                "success": False,
                "error": tr("file_manager.container_not_ready")
            }
        return self._container_proxy.run(action, payload)

    def _is_docker_mode(self) -> bool:
        if self.container_session and getattr(self.container_session, "mode", None) is not None:
            return getattr(self.container_session, "mode", None) == "docker"
        return (TERMINAL_SANDBOX_MODE or "").lower() == "docker" or bool(LINUX_SAFETY)

    def _is_host_mode(self) -> bool:
        if self.container_session and getattr(self.container_session, "mode", None) is not None:
            return getattr(self.container_session, "mode", None) != "docker"
        return (TERMINAL_SANDBOX_MODE or "").lower() == "host"
