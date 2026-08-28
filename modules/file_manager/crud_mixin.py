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

class CrudMixin:
    """FileManager crud mixin 能力 mixin。"""

    def create_file(self, path: str, content: str = "", file_type: str = "txt") -> Dict:
        """创建文件"""
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        
        # 添加文件扩展名
        if not full_path.suffix:
            full_path = full_path.with_suffix(f".{file_type}")
        ok, msg = self._ensure_host_access(full_path, "write")
        if not ok:
            return {"success": False, "error": msg}
        relative_path = self._relative_path(full_path)
        
        try:
            if self._use_container():
                result = self._container_call("create_file", {
                    "path": relative_path,
                    "content": ""
                })
                if result.get("success"):
                    print(f"{OUTPUT_FORMATS['file']} 创建文件: {relative_path}")
                return result

            # 创建父目录
            full_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write("")
            
            print(f"{OUTPUT_FORMATS['file']} 创建文件: {relative_path}")
            
            return {
                "success": True,
                "path": relative_path,
                "size": 0
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_file(self, path: str) -> Dict:
        """删除文件"""
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        
        if not full_path.exists():
            return {"success": False, "error": tr("file_manager.file_not_found")}
        
        if not full_path.is_file():
            return {"success": False, "error": tr("file_manager.not_a_file")}
        ok, msg = self._ensure_host_access(full_path, "write")
        if not ok:
            return {"success": False, "error": msg}
        
        try:
            relative_path = self._relative_path(full_path)
            if self._use_container():
                result = self._container_call("delete_file", {
                    "path": relative_path
                })
                if result.get("success"):
                    print(f"{OUTPUT_FORMATS['file']} 删除文件: {relative_path}")
                return result

            full_path.unlink()
            print(f"{OUTPUT_FORMATS['file']} 删除文件: {relative_path}")
            
            # 删除文件备注（如果存在）
            # 这需要通过context_manager处理，但file_manager没有直接访问权限
            # 所以返回相对路径，让调用者处理备注删除
            
            return {
                "success": True,
                "path": relative_path,
                "action": "deleted"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def rename_file(self, old_path: str, new_path: str) -> Dict:
        """重命名文件"""
        valid_old, error_old, full_old_path = self._validate_path(old_path)
        if not valid_old:
            return {"success": False, "error": error_old}
        
        valid_new, error_new, full_new_path = self._validate_path(new_path)
        if not valid_new:
            return {"success": False, "error": error_new}
        
        if not full_old_path.exists():
            return {"success": False, "error": tr("file_manager.original_not_found")}
        
        if full_new_path.exists():
            return {"success": False, "error": tr("file_manager.target_exists")}
        ok_old, msg_old = self._ensure_host_access(full_old_path, "write")
        if not ok_old:
            return {"success": False, "error": msg_old}
        ok_new, msg_new = self._ensure_host_access(full_new_path, "write")
        if not ok_new:
            return {"success": False, "error": msg_new}
        
        try:
            old_relative = self._relative_path(full_old_path)
            new_relative = self._relative_path(full_new_path)
            if self._use_container():
                result = self._container_call("rename_file", {
                    "old_path": old_relative,
                    "new_path": new_relative
                })
                if result.get("success"):
                    print(f"{OUTPUT_FORMATS['file']} 重命名: {old_relative} -> {new_relative}")
                return result

            full_old_path.rename(full_new_path)
            
            print(f"{OUTPUT_FORMATS['file']} 重命名: {old_relative} -> {new_relative}")
            
            return {
                "success": True,
                "old_path": old_relative,
                "new_path": new_relative,
                "action": "renamed"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_folder(self, path: str) -> Dict:
        """创建文件夹"""
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        
        if full_path.exists():
            return {"success": False, "error": tr("file_manager.folder_exists")}
        ok, msg = self._ensure_host_access(full_path, "write")
        if not ok:
            return {"success": False, "error": msg}
        
        try:
            relative_path = self._relative_path(full_path)
            if self._use_container():
                result = self._container_call("create_folder", {"path": relative_path})
                if result.get("success"):
                    print(f"{OUTPUT_FORMATS['file']} 创建文件夹: {relative_path}")
                return result

            full_path.mkdir(parents=True, exist_ok=True)
            print(f"{OUTPUT_FORMATS['file']} 创建文件夹: {relative_path}")
            
            return {"success": True, "path": relative_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_folder(self, path: str) -> Dict:
        """删除文件夹"""
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        
        if not full_path.exists():
            return {"success": False, "error": tr("file_manager.folder_not_found")}
        
        if not full_path.is_dir():
            return {"success": False, "error": tr("file_manager.not_a_folder")}
        
        try:
            relative_path = self._relative_path(full_path)
            if self._use_container():
                result = self._container_call("delete_folder", {"path": relative_path})
                if result.get("success"):
                    print(f"{OUTPUT_FORMATS['file']} 删除文件夹: {relative_path}")
                return result

            shutil.rmtree(full_path)
            print(f"{OUTPUT_FORMATS['file']} 删除文件夹: {relative_path}")
            
            return {"success": True, "path": relative_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_file(self, path: str, content: str, mode: str = "w") -> Dict:
        """
        写入文件
        
        Args:
            path: 文件路径
            content: 内容
            mode: 写入模式 - "w"(覆盖), "a"(追加)
        """
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        ok, msg = self._ensure_host_access(full_path, "write")
        if not ok:
            return {"success": False, "error": msg}
        
        # === 新增：内容预处理和验证 ===
        if content:
            # 长度检查
            if not DISABLE_LENGTH_CHECK and len(content) > 9999999999:  # 100KB限制
                return {
                    "success": False,
                    "error": tr("file_manager.content_too_long", length=len(content)),
                    "suggestion": "请分块处理或使用部分修改方式"
                }
            
            # 检查潜在的JSON格式问题
            if content.count('"') % 2 != 0:
                print(f"{OUTPUT_FORMATS['warning']} 检测到奇数个引号，可能存在格式问题")
            
            # 检查大量转义字符
            if content.count('\\') > len(content) / 20:
                print(f"{OUTPUT_FORMATS['warning']} 检测到大量转义字符，建议检查内容格式")
        
        try:
            relative_path = self._relative_path(full_path)
            if PROJECT_MAX_STORAGE_BYTES and self._is_docker_mode():
                current_size = self._get_project_size()
                existing_size = full_path.stat().st_size if full_path.exists() else 0
                if mode == "a":
                    projected_total = current_size + len(content)
                else:
                    projected_total = current_size - existing_size + len(content)
                if projected_total > PROJECT_MAX_STORAGE_BYTES:
                    return {
                        "success": False,
                        "error": tr("file_manager.storage_quota_exceeded"),
                        "limit_bytes": PROJECT_MAX_STORAGE_BYTES,
                        "project_size_bytes": current_size,
                        "attempt_size_bytes": len(content)
                    }
            # 记录写入前的原始内容（用于版本控制和前端 diff 显示）
            original_file: Optional[str] = None
            try:
                if full_path.exists():
                    original_file = full_path.read_text(encoding='utf-8')
            except Exception:
                original_file = None

            if self._use_container():
                result = self._container_call("write_file", {
                    "path": relative_path,
                    "content": content,
                    "mode": mode
                })
                if result.get("success"):
                    action = "覆盖" if mode == "w" else "追加"
                    print(f"{OUTPUT_FORMATS['file']} {action}文件: {relative_path}")
                    result["original_file"] = original_file
                return result

            # 创建父目录
            full_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(full_path, mode, encoding='utf-8') as f:
                f.write(content)
            
            action = "覆盖" if mode == "w" else "追加"
            print(f"{OUTPUT_FORMATS['file']} {action}文件: {relative_path}")
            
            # 读取写入后的实际内容（追加模式需要从文件读取）
            try:
                new_file = full_path.read_text(encoding='utf-8')
            except Exception:
                new_file = content
            
            return {
                "success": True,
                "path": relative_path,
                "size": len(content),
                "mode": mode,
                "original_file": original_file,
                "new_file": new_file,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def append_file(self, path: str, content: str) -> Dict:
        """追加内容到文件"""
        return self.write_file(path, content, mode="a")

    def clear_file(self, path: str) -> Dict:
        """清空文件内容"""
        return self.write_file(path, "", mode="w")
