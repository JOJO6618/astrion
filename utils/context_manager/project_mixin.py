# utils/context_manager.py - 上下文管理器（集成对话持久化和Token统计）

import os
import json
import base64
import mimetypes
import io
import uuid
import platform
import shutil
import subprocess
from copy import deepcopy
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime
try:
    from config import (
        MAX_CONTEXT_SIZE,
        DATA_DIR,
        PROMPTS_DIR,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
    )
    from config.model_profiles import (
        get_model_prompt_replacements,
        get_registered_model_keys,
        model_supports_image,
        model_supports_video,
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        MAX_CONTEXT_SIZE,
        DATA_DIR,
        PROMPTS_DIR,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        TERMINAL_SANDBOX_MODE,
        LINUX_SAFETY,
    )
    from config.model_profiles import (
        get_model_prompt_replacements,
        get_registered_model_keys,
        model_supports_image,
        model_supports_video,
    )
from utils.conversation_manager import ConversationManager
from utils.host_workspace_debug import write_host_workspace_debug
from utils.media_store import MediaStore
from utils.token_usage import normalize_usage_payload

from modules.i18n import tr

AUTO_SHALLOW_PLACEHOLDER = "过早的工具结果已经被自动压缩"
AUTO_SHALLOW_TOOL_WHITELIST = {
    "write_file",
    "read_file",
    "edit_file",
    "terminal_input",
    "terminal_snapshot",
    "web_search",
    "extract_webpage",
    "run_command",
    "view_image",
    "view_video",
}


class ProjectMixin:
    """ContextManager project mixin 能力 mixin。"""

    def _ensure_project_snapshot(self) -> Dict[str, Any]:
        """
        确保当前对话拥有项目文件树快照：
        - 若已缓存/存档则直接返回；
        - 若不存在，则第一次扫描目录并存入对话文件，后续复用。
        """
        if self.project_snapshot:
            return self.project_snapshot
        
        meta = self.conversation_metadata or {}
        stored_tree = meta.get("project_file_tree")
        if stored_tree and stored_tree != "宿主机模式下文件树不可用":
            use_stored_snapshot = True
            stored_path = meta.get("project_path")
            if isinstance(stored_path, str) and stored_path.strip():
                try:
                    stored_path_obj = Path(stored_path.strip()).expanduser().resolve()
                    if stored_path_obj != Path(self.project_path).resolve():
                        use_stored_snapshot = False
                        write_host_workspace_debug(
                            "context_manager.project_snapshot.discard_stale_snapshot",
                            conversation_id=self.current_conversation_id,
                            metadata_project_path=str(stored_path_obj),
                            current_project_path=str(Path(self.project_path).resolve()),
                        )
                except Exception:
                    pass
            if use_stored_snapshot:
                self.project_snapshot = {
                    "file_tree": stored_tree,
                    "statistics": meta.get("project_statistics"),
                    "snapshot_at": meta.get("project_snapshot_at")
                }
                write_host_workspace_debug(
                    "context_manager.project_snapshot.use_metadata_snapshot",
                    conversation_id=self.current_conversation_id,
                    project_path=str(self.project_path),
                )
                return self.project_snapshot
        
        # 首次生成并缓存
        structure = self._get_project_structure_for_prompt()
        snapshot = {
            "file_tree": self._build_file_tree(structure),
            "statistics": {
                "total_files": structure["total_files"],
                "total_size": structure["total_size"]
            },
            "snapshot_at": datetime.now().isoformat()
        }
        self.project_snapshot = snapshot
        write_host_workspace_debug(
            "context_manager.project_snapshot.generated_new",
            conversation_id=self.current_conversation_id,
            project_path=str(self.project_path),
            total_files=snapshot.get("statistics", {}).get("total_files"),
        )
        if self.current_conversation_id:
            self.conversation_manager.update_project_snapshot(
                self.current_conversation_id,
                project_file_tree=snapshot["file_tree"],
                project_statistics=snapshot["statistics"],
                project_snapshot_at=snapshot["snapshot_at"]
            )
            # 同步内存元数据
            self.conversation_metadata["project_file_tree"] = snapshot["file_tree"]
            self.conversation_metadata["project_statistics"] = snapshot["statistics"]
            self.conversation_metadata["project_snapshot_at"] = snapshot["snapshot_at"]
        return snapshot

    def _get_project_structure_for_prompt(self, limit: int = 20) -> Dict:
        """获取用于 prompt 的浅层文件结构（仅根目录，优先文件夹）。"""
        structure = {
            "path": str(self.project_path),
            "files": [],
            "folders": [],
            "total_files": 0,
            "total_size": 0,
            "tree": {}
        }
        if not self.project_path.exists():
            return structure
        try:
            entries = [p for p in self.project_path.iterdir() if not p.name.startswith('.')]
        except PermissionError:
            return structure

        folders = [p for p in entries if p.is_dir()]
        files = [p for p in entries if p.is_file()]
        folders.sort(key=lambda p: p.name.lower())
        files.sort(key=lambda p: p.name.lower())
        selected = (folders + files)[:max(0, limit)]

        def add_entry(entry: Path, *, display_path: Optional[str] = None):
            relative_path = str(entry.relative_to(self.project_path))
            if display_path:
                relative_path = display_path
            if entry.is_dir():
                structure["folders"].append({
                    "name": entry.name,
                    "path": relative_path
                })
                parts = relative_path.split("/")
                parent = structure["tree"]
                for idx, part in enumerate(parts):
                    is_leaf = idx == len(parts) - 1
                    parent.setdefault(part, {
                        "type": "folder",
                        "path": "/".join(parts[:idx + 1]),
                        "children": {}
                    })
                    if is_leaf:
                        parent[part]["type"] = "folder"
                        parent[part]["path"] = relative_path
                        parent[part].setdefault("children", {})
                    parent = parent[part].setdefault("children", {})
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                file_info = {
                    "name": entry.name,
                    "path": relative_path,
                    "size": size,
                    "annotation": self.file_annotations.get(relative_path, "")
                }
                structure["files"].append(file_info)
                structure["total_files"] += 1
                structure["total_size"] += size
                structure["tree"][entry.name] = {
                    "type": "file",
                    "path": relative_path,
                    "size": size,
                    "annotation": file_info["annotation"]
                }

        for entry in selected:
            add_entry(entry)

        # 根目录浅扫会跳过点目录；内部文件迁移到 .agents 后，单独展示这几个对模型有意义的目录。
        agents_dir = self.project_path / ".astrion"
        for internal_name in ("skills", "user_upload", "compact_result", "review"):
            internal_dir = agents_dir / internal_name
            if internal_dir.exists() and internal_dir.is_dir():
                add_entry(internal_dir, display_path=f".astrion/{internal_name}")
        return structure

    def get_project_structure(self) -> Dict:
        """获取项目文件结构"""
        structure = {
            "path": str(self.project_path),
            "files": [],
            "folders": [],
            "total_files": 0,
            "total_size": 0,
            "tree": {}  # 新增：树形结构数据
        }

        if self._is_host_mode_without_safety():
            structure["unavailable"] = True
            structure["message"] = tr("project_mixin.host_mode_tree_unavailable")
            return structure
        
        # 记录实际存在的文件
        existing_files = set()
        
        def scan_directory(path: Path, level: int = 0, max_level: int = 5, parent_tree: Dict = None):
            if level > max_level:
                return
            
            if parent_tree is None:
                parent_tree = structure["tree"]
            
            try:
                # 获取目录内容并排序（文件夹在前，文件在后）
                items = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
                
                for item in items:
                    if item.name.startswith('.'):
                        continue
                    
                    relative_path = str(item.relative_to(self.project_path))
                    
                    if item.is_file():
                        existing_files.add(relative_path)  # 记录存在的文件
                        file_info = {
                            "name": item.name,
                            "path": relative_path,
                            "size": item.stat().st_size,
                            "modified": datetime.fromtimestamp(item.stat().st_mtime).isoformat(),
                            "annotation": self.file_annotations.get(relative_path, "")
                        }
                        structure["files"].append(file_info)
                        structure["total_files"] += 1
                        structure["total_size"] += file_info["size"]
                        
                        # 添加到树形结构
                        parent_tree[item.name] = {
                            "type": "file",
                            "path": relative_path,
                            "size": file_info["size"],
                            "annotation": file_info["annotation"]
                        }
                    
                    elif item.is_dir():
                        folder_info = {
                            "name": item.name,
                            "path": relative_path
                        }
                        structure["folders"].append(folder_info)
                        
                        # 创建文件夹节点
                        parent_tree[item.name] = {
                            "type": "folder",
                            "path": relative_path,
                            "children": {}
                        }
                        
                        # 递归扫描子目录
                        scan_directory(item, level + 1, max_level, parent_tree[item.name]["children"])
            except PermissionError:
                pass
        
        scan_directory(self.project_path)
        
        # 清理不存在文件的备注
        invalid_annotations = []
        for annotation_path in self.file_annotations.keys():
            if annotation_path not in existing_files:
                invalid_annotations.append(annotation_path)
        
        if invalid_annotations:
            for path in invalid_annotations:
                del self.file_annotations[path]
                print(f"🧹 清理无效备注: {path}")
            self.save_annotations()
        
        return structure

    def _build_file_tree(self, structure: Dict) -> str:
        """构建文件树字符串（修复版：正确显示树形结构）"""
        if not structure.get("tree"):
            return f"📁 {structure['path']}/\n(空项目)"
        
        lines = []
        project_name = Path(structure['path']).name
        if self._is_host_mode_without_safety():
            root_label = f"{structure['path']} (项目根)"
        else:
            container_root = (self.container_mount_path or "").strip() or "/workspace"
            container_root = container_root.rstrip("/") or "/"
            if container_root == "/":
                root_label = f"/ (项目根)"
            elif container_root.endswith(project_name):
                root_label = f"{container_root} (项目根)"
            else:
                root_label = f"{container_root} (映射自 {project_name})"
        lines.append(f"{root_label}/")
        
        ROOT_FOLDER_CHILD_LIMIT = 20

        def count_descendants(item: Dict) -> int:
            """计算某个文件夹下（含多层）所有子项数量。"""
            if item.get("type") != "folder":
                return 0
            children = item.get("children") or {}
            total = len(children)
            for child in children.values():
                if child.get("type") == "folder":
                    total += count_descendants(child)
            return total

        def build_tree_recursive(tree_dict: Dict, prefix: str = "", depth: int = 0):
            """递归构建树形结构"""
            if not tree_dict:
                return
                
            # 将项目按类型和名称排序：文件夹在前，文件在后，同类型按名称排序
            items = list(tree_dict.items())
            folders = [(name, info) for name, info in items if info["type"] == "folder"]
            files = [(name, info) for name, info in items if info["type"] == "file"]
            
            # 排序
            folders.sort(key=lambda x: x[0].lower())
            files.sort(key=lambda x: x[0].lower())
            
            # 合并列表
            sorted_items = folders + files
            
            for i, (name, info) in enumerate(sorted_items):
                is_last = (i == len(sorted_items) - 1)
                
                # 选择连接符
                if is_last:
                    current_connector = "└── "
                    next_prefix = prefix + "    "
                else:
                    current_connector = "├── "
                    next_prefix = prefix + "│   "
                
                if info["type"] == "folder":
                    # 文件夹
                    lines.append(f"{prefix}{current_connector}{name}/")
                    
                    # 递归处理子项目
                    children = info.get("children") or {}
                    if depth == 0:
                        total_entries = count_descendants(info)
                    else:
                        total_entries = None
                    if depth == 0 and total_entries is not None and total_entries > ROOT_FOLDER_CHILD_LIMIT:
                        lines.append(
                            f"{next_prefix}… (该目录包含 {total_entries} 项，已省略以控制 prompt 体积)"
                        )
                    elif children:
                        build_tree_recursive(children, next_prefix, depth + 1)
                else:
                    # 文件
                    size_info = self._format_file_size(info['size'])
                    
                    # 构建文件行
                    file_line = f"{prefix}{current_connector}{name}"
                    
                    # 添加大小信息（简化版）
                    if info['size'] > 1024:  # 只显示大于1KB的文件大小
                        file_line += f" {size_info}"
                    
                    # 添加备注
                    if info.get('annotation'):
                        file_line += f" # {info['annotation']}"
                    
                    lines.append(file_line)
        
        # 构建树形结构
        build_tree_recursive(structure["tree"])
        
        # 添加统计信息
        lines.append("")
        lines.append(f"统计: {structure['total_files']} 个文件, {structure['total_size']/1024/1024:.2f}MB")
        
        return "\n".join(lines)

    def _format_file_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"({size_bytes}B)"
        elif size_bytes < 1024 * 1024:
            return f"({size_bytes/1024:.1f}KB)"
        else:
            return f"({size_bytes/1024/1024:.1f}MB)"

    def _get_file_icon(self, filename: str) -> str:
        """根据文件类型返回合适的图标（当前已禁用 emoji，返回空字符串）。"""
        return ""
