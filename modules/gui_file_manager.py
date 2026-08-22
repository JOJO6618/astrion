# modules/gui_file_manager.py - GUI 文件管理专用服务

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class FileEntry:
    """用于前端显示的文件/目录条目"""

    name: str
    path: str
    type: str  # file / directory
    size: int
    modified_at: float
    extension: Optional[str]
    is_editable: bool


class GuiFileManager:
    """面向 GUI 的文件管理器，实现桌面式操作所需的能力"""

    EDITABLE_EXTENSIONS = {
        ".txt",
        ".md",
        ".py",
        ".js",
        ".ts",
        ".json",
        ".yaml",
        ".yml",
        ".html",
        ".css",
        ".scss",
        ".less",
        ".xml",
        ".csv",
        ".ini",
        ".cfg",
        ".toml",
        ".sh",
        ".bat",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".vue",
        ".svelte",
        ".php",
        ".rb",
        ".swift",
        ".dart",
        ".sql",
    }

    MAX_TEXT_FILE_SIZE = 2 * 1024 * 1024  # 2MB

    def __init__(self, base_path: str):
        self.base_path = Path(base_path).expanduser().resolve()
        if not self.base_path.exists():
            raise ValueError("Base path does not exist")

    # -------------------------
    # 路径解析与安全检查
    # -------------------------

    def _resolve(self, relative: Optional[str]) -> Path:
        relative = (relative or "").strip()
        target = (self.base_path if not relative else (self.base_path / relative)).resolve()
        try:
            target.relative_to(self.base_path)
        except ValueError:
            raise ValueError("路径越界")
        return target

    def _to_relative(self, absolute: Path) -> str:
        return str(absolute.relative_to(self.base_path)).replace("\\", "/")

    def _unique_name(self, directory: Path, name: str) -> Path:
        candidate = directory / name
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        counter = 1
        while candidate.exists():
            candidate = directory / f"{stem}_copy{counter if counter > 1 else ''}{suffix}"
            counter += 1
        return candidate

    def _check_editable(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if path.stat().st_size > self.MAX_TEXT_FILE_SIZE:
            return False
        ext = path.suffix.lower()
        if ext in self.EDITABLE_EXTENSIONS:
            return True
        # 无扩展名的文本文件尝试 UTF-8 读取判断
        if not ext:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    fh.read(2048)
                return True
            except Exception:
                return False
        return False

    # -------------------------
    # 列表与元数据
    # -------------------------

    def list_directory(self, relative: Optional[str] = None) -> Tuple[str, List[FileEntry]]:
        directory = self._resolve(relative)
        if not directory.exists():
            raise FileNotFoundError("目录不存在")
        if not directory.is_dir():
            raise NotADirectoryError("目标不是目录")

        entries: List[FileEntry] = []
        for entry in sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            stat = entry.stat()
            entries.append(
                FileEntry(
                    name=entry.name,
                    path=self._to_relative(entry),
                    type="directory" if entry.is_dir() else "file",
                    size=stat.st_size,
                    modified_at=stat.st_mtime,
                    extension=entry.suffix.lower() if entry.is_file() else None,
                    is_editable=self._check_editable(entry),
                )
            )
        relative_path = "" if directory == self.base_path else self._to_relative(directory)
        return relative_path, entries

    def breadcrumb(self, relative: Optional[str]) -> List[Dict[str, str]]:
        crumbs: List[Dict[str, str]] = []
        directory = self._resolve(relative)
        try:
            directory.relative_to(self.base_path)
        except ValueError:
            raise ValueError("路径越界")

        current = directory
        while True:
            relative_path = "" if current == self.base_path else self._to_relative(current)
            crumbs.append({"name": current.name if current != self.base_path else "根目录", "path": relative_path})
            if current == self.base_path:
                break
            current = current.parent
        crumbs.reverse()
        return crumbs

    # -------------------------
    # 基本操作
    # -------------------------

    def create_entry(self, parent_relative: Optional[str], name: str, entry_type: str) -> str:
        parent = self._resolve(parent_relative)
        if not parent.exists():
            raise FileNotFoundError("父目录不存在")
        if not parent.is_dir():
            raise NotADirectoryError("父路径不是目录")

        sanitized = name.strip()
        if not sanitized:
            raise ValueError("名称不能为空")

        target = parent / sanitized
        if target.exists():
            raise FileExistsError("同名文件或目录已存在")

        if entry_type == "directory":
            target.mkdir(parents=False, exist_ok=False)
        elif entry_type == "file":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        else:
            raise ValueError("不支持的类型")

        return self._to_relative(target)

    def delete_entries(self, relative_paths: List[str]) -> Dict[str, str]:
        results: Dict[str, str] = {}
        for rel in relative_paths:
            target = self._resolve(rel)
            if not target.exists():
                results[rel] = "missing"
                continue
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                results[rel] = "deleted"
            except Exception as exc:
                results[rel] = f"error: {exc}"
        return results

    def rename_entry(self, relative_path: str, new_name: str) -> str:
        target = self._resolve(relative_path)
        if not target.exists():
            raise FileNotFoundError("目标不存在")

        parent = target.parent
        sanitized = new_name.strip()
        if not sanitized:
            raise ValueError("新名称不能为空")
        new_path = parent / sanitized
        if new_path.exists():
            raise FileExistsError("目标名称已存在")

        target.rename(new_path)
        return self._to_relative(new_path)

    def copy_entries(self, relative_paths: List[str], destination_relative: str) -> Dict[str, str]:
        destination = self._resolve(destination_relative)
        if not destination.exists() or not destination.is_dir():
            raise NotADirectoryError("目标目录不存在")

        results: Dict[str, str] = {}
        for rel in relative_paths:
            source = self._resolve(rel)
            if not source.exists():
                results[rel] = "missing"
                continue
            try:
                target = self._unique_name(destination, source.name)
                if source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
                results[rel] = self._to_relative(target)
            except Exception as exc:
                results[rel] = f"error: {exc}"
        return results

    def move_entries(self, relative_paths: List[str], destination_relative: str) -> Dict[str, str]:
        destination = self._resolve(destination_relative)
        if not destination.exists() or not destination.is_dir():
            raise NotADirectoryError("目标目录不存在")

        results: Dict[str, str] = {}
        for rel in relative_paths:
            source = self._resolve(rel)
            if not source.exists():
                results[rel] = "missing"
                continue
            try:
                target = destination / source.name
                if target.exists():
                    target = self._unique_name(destination, source.name)
                shutil.move(str(source), str(target))
                results[rel] = self._to_relative(target)
            except Exception as exc:
                results[rel] = f"error: {exc}"
        return results

    # -------------------------
    # 文本读写
    # -------------------------

    def read_text(self, relative_path: str) -> Tuple[str, str]:
        target = self._resolve(relative_path)
        if not target.exists():
            raise FileNotFoundError("文件不存在")
        if not target.is_file():
            raise IsADirectoryError("目标是目录")

        size = target.stat().st_size
        if size > self.MAX_TEXT_FILE_SIZE:
            raise ValueError("文件过大，暂不支持直接编辑")

        try:
            with open(target, "r", encoding="utf-8") as fh:
                content = fh.read()
        except UnicodeDecodeError as exc:
            raise ValueError(f"文件不是 UTF-8 编码: {exc}") from exc

        return content, datetime.fromtimestamp(target.stat().st_mtime).isoformat()

    def write_text(self, relative_path: str, content: str) -> Dict[str, str]:
        target = self._resolve(relative_path)
        if not target.exists():
            raise FileNotFoundError("文件不存在")
        if not target.is_file():
            raise IsADirectoryError("目标是目录")

        size = len(content.encode("utf-8"))
        if size > self.MAX_TEXT_FILE_SIZE:
            raise ValueError("内容过大，超出限制")

        with open(target, "w", encoding="utf-8") as fh:
            fh.write(content)

        stat = target.stat()
        return {
            "path": self._to_relative(target),
            "size": str(stat.st_size),
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }

    # -------------------------
    # 上传与下载
    # -------------------------

    def prepare_upload(self, destination_relative: Optional[str], filename: str) -> Path:
        destination = self._resolve(destination_relative)
        if not destination.exists():
            destination.mkdir(parents=True, exist_ok=True)
        if not destination.is_dir():
            raise NotADirectoryError("上传目标必须是目录")
        sanitized = filename.strip()
        if not sanitized:
            raise ValueError("文件名不能为空")
        target = destination / sanitized
        target = self._unique_name(destination, target.name) if target.exists() else target
        return target

    def prepare_download(self, relative_path: str) -> Path:
        target = self._resolve(relative_path)
        if not target.exists():
            raise FileNotFoundError("文件不存在")
        return target

