"""读取侧/环境侧测试共用的内存替身。

全部替身只操作内存字典，不触碰本机磁盘、不启动服务、不读真实配置。
调用方必须先执行 ``read_routing_env.prepare_isolated_runtime()`` 再导入本模块。
"""
from __future__ import annotations

from pathlib import Path

from core.main_terminal_parts.context.prompt import PromptMixin
from core.main_terminal_parts.tools_read import MainTerminalToolsReadMixin
from utils.context_manager.message_mixin import MessageMixin
from utils.context_manager.project_mixin import ProjectMixin
from utils.context_manager.runtime_mixin import RuntimeMixin

METADATA_FAILURE = {
    "success": False,
    "error": "执行后端元数据不可用（容器未就绪或路径越界）",
}

# 控制端本机不存在的项目目录：证明树/环境来自执行后端而非控制端磁盘。
CONTROLLER_PATH = "/__nonexistent_controller_workspace__/project"


class PoisonedFileManager:
    """一旦被读取就失败：用于证明读取走的是执行后端而不是本机 FileManager。"""

    def __getattr__(self, name):
        raise AssertionError(f"读取路径不应触碰本机 file_manager.{name}")


class FakeFiles:
    """内存 FileSystemBackend 替身（不触碰本机磁盘）。"""

    def __init__(self, *, files=None, listings=None, metas=None, metadata_failure=False):
        self.files = dict(files or {})
        self.listings = dict(listings or {})
        self.metas = dict(metas or {})
        self.metadata_failure = metadata_failure
        self.calls = []

    # -- 内容 ----------------------------------------------------------
    def read_file(self, path):
        self.calls.append(("read_file", path))
        if path in self.files:
            content = self.files[path]
            return {"success": True, "path": path, "content": content,
                    "size": len(content.encode("utf-8"))}
        return {"success": False, "error": f"file not found: {path}"}

    def read_text_segment(self, path, *, start_line=None, end_line=None, size_limit=None):
        self.calls.append(("read_text_segment", path, start_line, end_line))
        if path not in self.files:
            return {"success": False, "error": f"file not found: {path}"}
        content = self.files[path]
        lines = content.splitlines(keepends=True)
        total = len(lines)
        start = 1 if start_line is None else int(start_line)
        end = total if end_line is None else min(int(end_line), total)
        return {
            "success": True,
            "path": path,
            "content": "".join(lines[start - 1:end]),
            "size": len(content.encode("utf-8")),
            "line_start": start,
            "line_end": end,
            "total_lines": total,
        }

    def search_text(self, path, *, query, max_matches, context_before,
                    context_after, case_sensitive=False, size_limit=None):
        self.calls.append(("search_text", path, query))
        if path not in self.files:
            return {"success": False, "error": f"file not found: {path}"}
        content = self.files[path]
        return {
            "success": True,
            "path": path,
            "size": len(content.encode("utf-8")),
            "matches": [
                {"line_start": 2, "line_end": 2, "hits": 1, "snippet": "beta line"},
                {"line_start": 3, "line_end": 3, "hits": 1, "snippet": "gamma line"},
            ][:max_matches],
        }

    def extract_segments(self, path, segments, *, size_limit=None):
        self.calls.append(("extract_segments", path, len(segments)))
        if path not in self.files:
            return {"success": False, "error": f"file not found: {path}"}
        lines = self.files[path].splitlines()
        out = []
        for segment in segments:
            start = int(segment.get("start_line") or 1)
            end = int(segment.get("end_line") or len(lines))
            out.append({
                "label": segment.get("label") or "",
                "line_start": start,
                "line_end": end,
                "content": "\n".join(lines[start - 1:end]),
            })
        return {
            "success": True,
            "path": path,
            "size": len(self.files[path].encode("utf-8")),
            "total_lines": len(lines),
            "segments": out,
        }

    def read_binary(self, path):
        self.calls.append(("read_binary", path))
        return self.files[path].encode("utf-8")

    # -- 元数据 --------------------------------------------------------
    def inspect_path(self, path, access="read"):
        self.calls.append(("inspect_path", path, access))
        if self.metadata_failure:
            return dict(METADATA_FAILURE)
        meta = self.metas.get(path)
        if meta is None:
            return {
                "success": True, "path": path, "relative_path": path,
                "exists": False, "is_file": False, "is_dir": False,
                "within_workspace": True, "suffix": None, "size": None, "mtime": None,
            }
        return {
            "success": True,
            "path": path,
            "relative_path": path,
            "exists": True,
            "is_file": bool(meta.get("is_file")),
            "is_dir": bool(meta.get("is_dir")),
            "within_workspace": True,
            "suffix": meta.get("suffix"),
            "size": meta.get("size"),
            "mtime": meta.get("mtime"),
        }

    def list_files(self, path=""):
        self.calls.append(("list_files", path))
        if self.metadata_failure:
            return dict(METADATA_FAILURE)
        entry = self.listings.get(path) or {}
        return {
            "success": True,
            "path": path or ".",
            "files": list(entry.get("files") or []),
            "folders": list(entry.get("folders") or []),
        }

    # -- 断言辅助 ------------------------------------------------------
    def content_calls(self):
        return [call for call in self.calls if call[0] in {
            "read_file", "read_text_segment", "search_text", "extract_segments"
        }]


class FakeBackend:
    backend_name = "fake"

    def __init__(self, files: FakeFiles, environment=None):
        self.files = files
        self._environment = environment

    def get_environment(self):
        if self._environment is None:
            return {
                "backend": "fake",
                "is_local": False,
                "platform": "fake",
                "execution_target": "host",
                "execution_mode": "sandbox",
                "network_permission": "restricted",
                "workspace_root": "/workspace",
                "cwd": "/workspace",
                "container": None,
            }
        return dict(self._environment)


class RecordingContextManager:
    def __init__(self):
        self.loaded = []

    def load_file(self, file_path, content=None):
        self.loaded.append((file_path, content))
        return True


class ReadToolStub(MainTerminalToolsReadMixin):
    """只带 mixin 能力的最小终端替身。"""

    def __init__(self, fake_files, *, project_path="/workspace"):
        self.execution_backend = FakeBackend(fake_files)
        self.file_manager = PoisonedFileManager()
        self.context_manager = RecordingContextManager()
        self.project_path = project_path
        self.data_dir = project_path


class PromptStub(PromptMixin):
    def __init__(self, fake_files, *, project_path="/workspace"):
        self.execution_backend = FakeBackend(fake_files)
        self.file_manager = PoisonedFileManager()
        self.project_path = Path(project_path)


class RecordingConversationManager:
    def __init__(self):
        self.snapshots = []

    def update_project_snapshot(self, conversation_id, **kwargs):
        self.snapshots.append((conversation_id, kwargs))


class ProjectStub(ProjectMixin):
    def __init__(self, fake_files, *, annotations=None, project_path="/workspace",
                 environment=None, metadata=None):
        self.execution_backend = FakeBackend(fake_files, environment=environment)
        self.file_manager = PoisonedFileManager()
        self.project_path = Path(project_path)
        self.container_mount_path = "/workspace"
        self.file_annotations = dict(annotations or {})
        self.conversation_metadata = dict(metadata or {})
        self.project_snapshot = None
        self.current_conversation_id = "conv-1"
        self.conversation_manager = RecordingConversationManager()

    def _is_host_mode_without_safety(self):
        return False


class EnvironmentDisplayStub(RuntimeMixin):
    """只带 RuntimeMixin 的环境显示替身（不启动终端、不读本机磁盘）。"""

    def __init__(self, environment, *, project_path=CONTROLLER_PATH,
                 host_cache=None, container_mount_path="/workspace"):
        self.execution_backend = FakeBackend(FakeFiles(), environment=environment)
        self.project_path = Path(project_path)
        self.container_mount_path = container_mount_path
        self.container_cpu_limit = "2"
        self.container_memory_limit = "4G"
        self.project_storage_limit = "512MB"
        self._host_runtime_cache = dict(host_cache or {})

    def _is_host_mode_without_safety(self):
        return False


class WorkspaceMessageStub(MessageMixin, RuntimeMixin):
    """工作区系统消息替身：验证 message_mixin 使用后端环境事实。"""

    TEMPLATE = (
        "name={project_name}\n"
        "runtime_environment={runtime_environment}\n"
        "resource_limit={resource_limit}\n"
        "container_path={container_path}\n"
        "file_tree={file_tree}\n"
    )

    def __init__(self, environment, *, project_path=CONTROLLER_PATH,
                 container_mount_path="/workspace"):
        self.execution_backend = FakeBackend(FakeFiles(), environment=environment)
        self.project_path = Path(project_path)
        self.container_mount_path = container_mount_path
        self.container_cpu_limit = "2"
        self.container_memory_limit = "4G"
        self.project_storage_limit = "512MB"
        self._host_runtime_cache = {}

    def _is_host_mode_without_safety(self):
        return False

    def load_prompt(self, _name):
        return self.TEMPLATE

    def _build_recent_uploads_block(self):
        return ""
