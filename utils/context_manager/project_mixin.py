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
from utils.context_manager.fs_access import (
    execution_environment,
    files_for_context,
    inspect_meta,
    list_dir,
    workspace_identity,
)
from utils.host_workspace_debug import write_host_workspace_debug
from utils.logger import setup_logger
from utils.media_store import MediaStore
from utils.token_usage import normalize_usage_payload

from modules.i18n import tr

logger = setup_logger(__name__)


def _lexical_path(value: Any) -> str:
    """路径词法归一化：只用 expanduser/normpath/normcase，不触碰文件系统。

    控制侧不允许对（可能属于远端的）路径做 ``Path.resolve()``/``exists()``。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return os.path.normcase(os.path.normpath(os.path.expanduser(text)))
    except Exception:
        return text

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
                if not self._stored_project_path_matches(stored_path):
                    use_stored_snapshot = False
                    write_host_workspace_debug(
                        "context_manager.project_snapshot.discard_stale_snapshot",
                        conversation_id=self.current_conversation_id,
                        metadata_project_path=_lexical_path(stored_path),
                        current_project_path=self._workspace_identity_hint(),
                    )
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
            listing_unavailable=bool(structure.get("listing_unavailable")),
        )
        if structure.get("listing_unavailable"):
            # 执行后端元数据能力缺口（如容器代理暂无 stat/list）：本次不把空树
            # 写进对话历史，保持原有 freeze 语义——“不做任务间重写历史”。
            return snapshot
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

    def _workspace_identity_hint(self) -> str:
        """当前工作区身份文案（仅用于日志/对比）：后端身份优先，其次词法路径。"""
        identity = workspace_identity(self)
        if identity:
            return _lexical_path(identity)
        return _lexical_path(self.project_path)

    def _stored_project_path_matches(self, stored_path: str) -> bool:
        """metadata 里的 project_path 是否指向当前工作区。

    优先用执行后端声明的身份（远端=节点路径），其次与当前工作区做纯词法比较；
    控制侧不对任何候选路径做 ``Path.resolve()``/``exists()``。
    """
        stored = _lexical_path(stored_path)
        if not stored:
            return True
        candidates = {_lexical_path(self.project_path)}
        identity = workspace_identity(self)
        if identity:
            candidates.add(_lexical_path(identity))
        candidates.discard("")
        return stored in candidates

    def _get_project_structure_for_prompt(self, limit: int = 20) -> Dict:
        """获取用于 prompt 的浅层文件结构（仅根目录，优先文件夹）。

        目录列举与文件大小经执行后端（本机后端 = FileManager，远端 = 节点
        元数据）；控制端不再对本机 Path 做 iterdir/stat。元数据能力缺口时
        设 ``listing_unavailable``，调用方据此不把空树写进对话历史。
        """
        structure = {
            "path": str(self.project_path),
            "files": [],
            "folders": [],
            "total_files": 0,
            "total_size": 0,
            "tree": {}
        }
        try:
            files_access = files_for_context(self)
            listing = list_dir(files_access, "")
        except Exception as exc:
            logger.warning(f"[project] 工作区根目录列举失败: {exc}")
            structure["listing_unavailable"] = True
            return structure
        if not listing.get("success"):
            logger.warning(
                "[project] 工作区根目录列举失败: %s",
                listing.get("error") or listing.get("code"),
            )
            structure["listing_unavailable"] = True
            return structure

        folder_entries = [
            {
                "kind": "folder",
                "name": str(item.get("name") or ""),
                "path": str(item.get("path") or item.get("name") or ""),
            }
            for item in (listing.get("folders") or [])
            if item.get("name")
        ]
        file_entries = [
            {
                "kind": "file",
                "name": str(item.get("name") or ""),
                "path": str(item.get("path") or item.get("name") or ""),
                "size": int(item.get("size") or 0),
            }
            for item in (listing.get("files") or [])
            if item.get("name")
        ]
        folder_entries.sort(key=lambda item: item["name"].lower())
        file_entries.sort(key=lambda item: item["name"].lower())
        selected = (folder_entries + file_entries)[: max(0, limit)]

        def add_folder(name: str, display_path: str):
            structure["folders"].append({
                "name": name,
                "path": display_path
            })
            parts = display_path.split("/")
            parent = structure["tree"]
            for idx, part in enumerate(parts):
                is_leaf = idx == len(parts) - 1
                node = parent.setdefault(part, {
                    "type": "folder",
                    "path": "/".join(parts[:idx + 1]),
                    "children": {}
                })
                if is_leaf:
                    node["type"] = "folder"
                    node["path"] = display_path
                    node.setdefault("children", {})
                parent = node.setdefault("children", {})

        def add_file(name: str, display_path: str, size: int):
            annotation = self.file_annotations.get(display_path, "")
            file_info = {
                "name": name,
                "path": display_path,
                "size": size,
                "annotation": annotation
            }
            structure["files"].append(file_info)
            structure["total_files"] += 1
            structure["total_size"] += size
            structure["tree"][name] = {
                "type": "file",
                "path": display_path,
                "size": size,
                "annotation": annotation
            }

        for entry in selected:
            if entry["kind"] == "folder":
                add_folder(entry["name"], entry["path"])
            else:
                add_file(entry["name"], entry["path"], entry["size"])

        # 根目录浅扫会跳过点目录；内部文件迁移到 .astrion 后，单独展示这几个对模型有意义的目录。
        for internal_name in ("skills", "user_upload", "compact_result", "review"):
            display_path = f".astrion/{internal_name}"
            meta = inspect_meta(files_access, display_path)
            if meta.get("success") and meta.get("is_dir"):
                add_folder(internal_name, display_path)
        return structure

    def get_project_structure(self) -> Dict:
        """获取项目文件结构（递归，最深 5 层）。

        目录列举与文件大小/时间全部经执行后端（``list_files`` +
        ``inspect_path``），控制侧不再对工作区做 ``iterdir/stat``。
        """
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

        try:
            files_access = files_for_context(self)
        except Exception as exc:
            logger.warning("[project] 文件树无法取得执行后端: %s", exc)
            return structure

        # 记录实际存在的文件
        existing_files = set()

        def list_children(relative_path: str):
            """列出一层目录：文件夹在前、名称排序（隐藏项不返回）。"""
            listing = list_dir(files_access, relative_path)
            if not listing.get("success"):
                logger.warning(
                    "[project] 目录列举失败 %s: %s",
                    relative_path or ".",
                    listing.get("error") or listing.get("code"),
                )
                return []
            folders = [
                {
                    "kind": "folder",
                    "name": str(item.get("name") or ""),
                    "path": str(item.get("path") or item.get("name") or ""),
                }
                for item in (listing.get("folders") or [])
                if item.get("name") and not str(item.get("name")).startswith(".")
            ]
            files = [
                {
                    "kind": "file",
                    "name": str(item.get("name") or ""),
                    "path": str(item.get("path") or item.get("name") or ""),
                    "size": int(item.get("size") or 0),
                    "modified": str(item.get("modified") or ""),
                }
                for item in (listing.get("files") or [])
                if item.get("name") and not str(item.get("name")).startswith(".")
            ]
            folders.sort(key=lambda item: item["name"].lower())
            files.sort(key=lambda item: item["name"].lower())
            return folders + files

        def scan_directory(relative_path: str, level: int = 0, max_level: int = 5, parent_tree: Dict = None):
            if level > max_level:
                return

            if parent_tree is None:
                parent_tree = structure["tree"]

            for entry in list_children(relative_path):
                name = entry["name"]
                relative_entry = entry["path"] or name

                if entry["kind"] == "file":
                    existing_files.add(relative_entry)  # 记录存在的文件
                    file_info = {
                        "name": name,
                        "path": relative_entry,
                        "size": entry["size"],
                        "modified": entry["modified"],
                        "annotation": self.file_annotations.get(relative_entry, ""),
                    }
                    structure["files"].append(file_info)
                    structure["total_files"] += 1
                    structure["total_size"] += file_info["size"]

                    # 添加到树形结构
                    parent_tree[name] = {
                        "type": "file",
                        "path": relative_entry,
                        "size": file_info["size"],
                        "annotation": file_info["annotation"],
                    }
                    continue

                structure["folders"].append({
                    "name": name,
                    "path": relative_entry,
                })

                # 创建文件夹节点
                parent_tree[name] = {
                    "type": "folder",
                    "path": relative_entry,
                    "children": {},
                }

                # 递归扫描子目录
                scan_directory(
                    relative_entry,
                    level + 1,
                    max_level,
                    parent_tree[name]["children"],
                )

        scan_directory("")

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

    def _file_tree_root_path(self, structure: Dict) -> str:
        """文件树根路径：绑定后端时只用后端上报路径，缺失则明确未知。

        ``execution_environment`` 返回 ``None`` 说明完全没有绑定后端，才允许
        沿用控制端结构里的路径（纯 ContextManager 场景）。
        """
        env = execution_environment(self)
        if env is None:
            return str(structure.get("path") or "")
        container = env.get("container") if isinstance(env.get("container"), dict) else {}
        container_root = str(container.get("mount_path") or "").strip()
        workspace = str(env.get("workspace_root") or env.get("cwd") or "").strip()
        target = str(env.get("execution_target") or "").strip().lower()
        if target == "docker" and container_root:
            return container_root.rstrip("/") or "/"
        return workspace or container_root or "（执行后端未上报工作区路径）"

    def _container_root_label(self, structure: Dict, project_name: str, container_root: str) -> str:
        """容器挂载根标签。"""
        container_root = (container_root or "").strip().rstrip("/") or "/"
        if container_root == "/":
            return "/ (项目根)"
        if container_root.endswith(project_name):
            return f"{container_root} (项目根)"
        return f"{container_root} (映射自 {project_name})"

    def _build_file_tree(self, structure: Dict) -> str:
        """构建文件树字符串（修复版：正确显示树形结构）"""
        if structure.get("listing_unavailable"):
            return "（文件树暂不可用：执行后端未返回目录元数据）"
        if not structure.get("tree"):
            return f"📁 {self._file_tree_root_path(structure)}/\n(空项目)"
        
        lines = []
        project_name = Path(str(structure.get('path') or '')).name
        # 绑定执行后端时，平台/路径/模式一律用后端事实（get_environment），
        # 不用控制端配置或控制端 OS 冒充执行目标。
        env = execution_environment(self)
        env_target = str((env or {}).get("execution_target") or "").strip().lower()
        if env is None:
            if self._is_host_mode_without_safety():
                root_label = f"{structure['path']} (项目根)"
            else:
                root_label = self._container_root_label(
                    structure, project_name, self.container_mount_path or "/workspace"
                )
        elif env_target != "docker":
            root_label = f"{self._file_tree_root_path(structure)} (项目根)"
        else:
            root_label = self._container_root_label(
                structure, project_name, self._file_tree_root_path(structure)
            )
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
