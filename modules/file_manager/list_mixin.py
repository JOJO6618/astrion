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

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle

# 临时禁用长度检查
DISABLE_LENGTH_CHECK = True

logger = setup_logger(__name__)

class ListMixin:
    """FileManager list mixin 能力 mixin。"""

    def edit_lines_range(self, path: str, start_line: int, end_line: int, content: str, operation: str) -> Dict:
        """
        基于行号编辑文件
        
        Args:
            path: 文件路径
            start_line: 起始行号（从1开始）
            end_line: 结束行号（从1开始，包含）
            content: 新内容
            operation: 操作类型 - "replace", "insert", "delete"
        """
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        
        if not full_path.exists():
            return {"success": False, "error": "文件不存在"}
        
        if not full_path.is_file():
            return {"success": False, "error": "不是文件"}
        
        # 验证行号
        if start_line < 1:
            return {"success": False, "error": "行号必须从1开始"}
        
        if end_line < start_line:
            return {"success": False, "error": "结束行号不能小于起始行号"}
        
        try:
            relative_path = self._relative_path(full_path)
            if self._use_container():
                return self._container_call("edit_lines_range", {
                    "path": relative_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "content": content,
                    "operation": operation
                })

            # 读取文件内容
            with open(full_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            total_lines = len(lines)
            
            # 检查行号范围
            if start_line > total_lines:
                if operation == "insert":
                    # 插入操作允许在文件末尾后插入
                    lines.extend([''] * (start_line - total_lines - 1))
                    lines.append(content if content.endswith('\n') else content + '\n')
                else:
                    return {"success": False, "error": f"起始行号 {start_line} 超出文件范围 (共 {total_lines} 行)"}
            elif end_line > total_lines:
                return {"success": False, "error": f"结束行号 {end_line} 超出文件范围 (共 {total_lines} 行)"}
            else:
                # 执行操作（转换为0基索引）
                start_idx = start_line - 1
                end_idx = end_line
                
                if operation == "replace":
                    # 替换指定行范围
                    new_lines = content.split('\n') if '\n' in content else [content]
                    # 确保每行都有换行符，除了最后一行需要检查原文件格式
                    formatted_lines = []
                    for i, line in enumerate(new_lines):
                        if i < len(new_lines) - 1 or (end_idx < len(lines) and lines[end_idx - 1].endswith('\n')):
                            formatted_lines.append(line + '\n' if not line.endswith('\n') else line)
                        else:
                            formatted_lines.append(line)
                    
                    lines[start_idx:end_idx] = formatted_lines
                    affected_lines = end_line - start_line + 1
                    
                elif operation == "insert":
                    # 在指定行前插入内容
                    new_lines = content.split('\n') if '\n' in content else [content]
                    formatted_lines = [line + '\n' if not line.endswith('\n') else line for line in new_lines]
                    lines[start_idx:start_idx] = formatted_lines
                    affected_lines = len(formatted_lines)
                    
                elif operation == "delete":
                    # 删除指定行范围
                    affected_lines = end_line - start_line + 1
                    del lines[start_idx:end_idx]
                    
                else:
                    return {"success": False, "error": f"未知的操作类型: {operation}"}
            
            # 写回文件
            with open(full_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            relative_path = self._relative_path(full_path)
            
            # 生成操作描述
            if operation == "replace":
                operation_desc = f"替换第 {start_line}-{end_line} 行"
            elif operation == "insert":
                operation_desc = f"在第 {start_line} 行前插入"
            elif operation == "delete":
                operation_desc = f"删除第 {start_line}-{end_line} 行"
            
            print(f"{OUTPUT_FORMATS['file']} {operation_desc}: {relative_path}")
            
            return {
                "success": True,
                "path": relative_path,
                "operation": operation,
                "start_line": start_line,
                "end_line": end_line,
                "affected_lines": affected_lines,
                "total_lines_after": len(lines),
                "description": operation_desc
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_files(self, path: str = "") -> Dict:
        """列出目录内容"""
        if path:
            valid, error, full_path = self._validate_path(path)
            if not valid:
                return {"success": False, "error": error}
        else:
            full_path = self.project_path
        
        if not full_path.exists():
            return {"success": False, "error": "目录不存在"}
        
        if not full_path.is_dir():
            return {"success": False, "error": "不是目录"}
        
        try:
            files = []
            folders = []
            
            for item in full_path.iterdir():
                if item.name.startswith('.'):
                    continue
                
                relative_path = self._relative_path(item)
                
                if item.is_file():
                    files.append({
                        "name": item.name,
                        "path": relative_path,
                        "size": item.stat().st_size,
                        "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat()
                    })
                elif item.is_dir():
                    folders.append({
                        "name": item.name,
                        "path": relative_path
                    })
            
            return {
                "success": True,
                "path": self._relative_path(full_path) if path else ".",
                "files": sorted(files, key=lambda x: x["name"]),
                "folders": sorted(folders, key=lambda x: x["name"])
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_file_info(self, path: str) -> Dict:
        """获取文件信息"""
        valid, error, full_path = self._validate_path(path)
        if not valid:
            return {"success": False, "error": error}
        
        if not full_path.exists():
            return {"success": False, "error": "文件不存在"}
        
        try:
            stat = full_path.stat()
            relative_path = self._relative_path(full_path)
            
            return {
                "success": True,
                "path": relative_path,
                "name": full_path.name,
                "type": "file" if full_path.is_file() else "folder",
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "extension": full_path.suffix if full_path.is_file() else None
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
