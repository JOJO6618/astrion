"""公共文件执行层：FileSystemBackend 协议 + LocalFileSystem 薄适配。

定位
----
Runtime/工具编排层只依赖 ``FileSystemBackend`` 协议；真实文件能力仍由
``modules.file_manager.FileManager`` 承担（路径校验、host 访问授权、
scoped_io、符号链接/硬链接与容器语义都在原链路里）。本模块只做**显式转发**，
不新增任何权限规则，也没有 ``__getattr__`` 万能代理。

元数据方法（``list_files/get_file_info/inspect_path/resolve_path``）在
``files_metadata.MetadataFileSystemMixin`` 中：宿主模式先授权再 stat；
容器模式经容器代理的 policy-checked 动作读取，不在宿主 stat，也不静默回退。
内容读写仍经 FileManager → 容器代理。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, runtime_checkable

from modules.execution_plane.files_metadata import MetadataFileSystemMixin
from modules.i18n import tr

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型提示，避免运行期循环导入
    from modules.file_manager import FileManager


@runtime_checkable
class FileSystemBackend(Protocol):
    """文件执行后端协议（仅承诺文件语义，不承诺安全边界）。

    安全边界仍由实现方保留：路径校验、host 访问授权、scoped_io 与容器语义
    由 ``LocalFileSystem`` 内的 FileManager 链路承担；换成远端实现时必须
    复刻同等边界。
    """

    def read_file(self, path: str) -> Dict[str, Any]:
        """读取整文件。返回 success/path/content/size 或 success=False/error。"""
        ...

    def read_text_segment(
        self,
        path: str,
        *,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        size_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """按行范围读取文本片段。"""
        ...

    def search_text(
        self,
        path: str,
        *,
        query: str,
        max_matches: int,
        context_before: int,
        context_after: int,
        case_sensitive: bool = False,
        size_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """文件内关键词搜索（返回合并后的上下文窗口）。"""
        ...

    def extract_segments(
        self,
        path: str,
        segments: List[Dict[str, Any]],
        *,
        size_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """按多个行区间提取内容。"""
        ...

    def read_binary(self, path: str) -> bytes:
        """读取二进制内容；拒绝时抛 PermissionError，保持原有安全读取语义。"""
        ...

    def write_binary(self, path: str, content: bytes) -> Dict[str, Any]:
        """精确写入二进制内容（内部浅版本恢复用）；只接受 bytes-like。"""
        ...

    def write_file(self, path: str, content: str, mode: str = "w") -> Dict[str, Any]:
        """写入文件（"w" 覆盖 / "a" 追加）。"""
        ...

    def replace_many_in_file(
        self, path: str, replacements: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """按顺序执行多组精确字符串替换；任意一组失败时不写入。"""
        ...

    def create_file(self, path: str, content: str = "", file_type: str = "txt") -> Dict[str, Any]:
        """创建文件（无后缀时补 file_type）。"""
        ...

    def delete_file(self, path: str) -> Dict[str, Any]:
        """删除文件。"""
        ...

    def rename_file(self, old_path: str, new_path: str) -> Dict[str, Any]:
        """重命名文件。"""
        ...

    def create_folder(self, path: str) -> Dict[str, Any]:
        """创建目录。"""
        ...

    def delete_folder(self, path: str) -> Dict[str, Any]:
        """递归删除目录（保留原工作区根删除保护）。"""
        ...

    def list_files(self, path: str = "") -> Dict[str, Any]:
        """列出目录内容（隐藏项与被拒绝项不返回）。"""
        ...

    def get_file_info(self, path: str) -> Dict[str, Any]:
        """兼容 FileManager 的旧元数据结构（ISO 时间字符串）。"""
        ...

    def inspect_path(self, path: str, access: str = "read") -> Dict[str, Any]:
        """授权后返回路径元数据；不存在也返回 success=True（exists=False）。"""
        ...

    def resolve_path(self, path: str, access: str = "read") -> Dict[str, Any]:
        """在执行侧解析并授权路径，返回工作区归属；控制端不得自行 resolve。"""
        ...


class LocalFileSystem(MetadataFileSystemMixin):
    """``FileSystemBackend`` 的本地 FileManager 薄适配器。

    内容读写原样转调 FileManager（签名、返回结构、异常均保留）；元数据由
    ``MetadataFileSystemMixin`` 提供，宿主模式授权先于 stat，容器模式走
    容器代理，绝不使用宿主镜像冒充容器视图。
    """

    def __init__(self, file_manager: "FileManager") -> None:
        self._file_manager = file_manager

    # ------------------------------------------------------------------
    # 内容读写：显式转发，不改变现有 FileManager 行为
    # ------------------------------------------------------------------
    def read_file(self, path: str) -> Dict[str, Any]:
        return self._file_manager.read_file(path)

    def read_text_segment(
        self,
        path: str,
        *,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        size_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._file_manager.read_text_segment(
            path,
            start_line=start_line,
            end_line=end_line,
            size_limit=size_limit,
        )

    def search_text(
        self,
        path: str,
        *,
        query: str,
        max_matches: int,
        context_before: int,
        context_after: int,
        case_sensitive: bool = False,
        size_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._file_manager.search_text(
            path,
            query=query,
            max_matches=max_matches,
            context_before=context_before,
            context_after=context_after,
            case_sensitive=case_sensitive,
            size_limit=size_limit,
        )

    def extract_segments(
        self,
        path: str,
        segments: List[Dict[str, Any]],
        *,
        size_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._file_manager.extract_segments(
            path, segments, size_limit=size_limit
        )

    def read_binary(self, path: str) -> bytes:
        # FileManager.read_binary 的宿主分支要求可授权路径（原调用方都传绝对路径）。
        # 协议层统一允许工作区相对路径：先用同一份 _validate_path 解析（不新增规则），
        # 其余 host 授权 / scoped_io / 容器 read_binary 语义仍由 FileManager 承担。
        if Path(path).is_absolute():
            return self._file_manager.read_binary(path)
        valid, error, full_path = self._file_manager._validate_path(path)
        if not valid or full_path is None:
            raise PermissionError(error or tr("file_manager.path_outside_project"))
        return self._file_manager.read_binary(str(full_path))

    def write_binary(self, path: str, content: bytes) -> Dict[str, Any]:
        """精确写回原始字节；非 bytes 直接拒绝，不做文本编码/解码回退。"""
        if not isinstance(content, (bytes, bytearray, memoryview)):
            return {
                "success": False,
                "error": "write_binary 仅接受 bytes，不做文本编码回退",
            }
        return self._file_manager.write_binary(path, bytes(content))

    def write_file(self, path: str, content: str, mode: str = "w") -> Dict[str, Any]:
        return self._file_manager.write_file(path, content, mode=mode)

    def replace_many_in_file(
        self, path: str, replacements: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return self._file_manager.replace_many_in_file(path, replacements)

    def create_file(self, path: str, content: str = "", file_type: str = "txt") -> Dict[str, Any]:
        return self._file_manager.create_file(path, content=content, file_type=file_type)

    def delete_file(self, path: str) -> Dict[str, Any]:
        return self._file_manager.delete_file(path)

    def rename_file(self, old_path: str, new_path: str) -> Dict[str, Any]:
        return self._file_manager.rename_file(old_path, new_path)

    def create_folder(self, path: str) -> Dict[str, Any]:
        return self._file_manager.create_folder(path)

    def delete_folder(self, path: str) -> Dict[str, Any]:
        return self._file_manager.delete_folder(path)


__all__ = ["FileSystemBackend", "LocalFileSystem"]
