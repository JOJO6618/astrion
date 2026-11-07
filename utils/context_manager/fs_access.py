"""执行侧文件读取入口（read 工具 / ContextManager / Prompt 装配共用）。

统一来源
--------
- 终端 owner（MainTerminal / WebTerminal）：
  ``from modules.execution_plane import get_execution_backend`` →
  ``get_execution_backend(owner).files``。后端由装配决定（本机或远端），
  一旦绑定就以其为准：读取失败原样返回/抛出，**不换实现、不回退本机**。
- 纯 ContextManager（没有 main_terminal）：这是唯一允许“明确构造本机实例”的
  场景——``FileManager`` + ``LocalFileSystem``，并缓存在该 ContextManager 上。
- 轻量测试 owner（只有 ``file_manager``、没有执行资源）：同样按明确的本机实例
  处理，而不是伪装成远端后端；这也保证既有 standalone 单测可跑。

说明
----
本模块只做“解析 + 结果透传”，不复刻 FileManager 的路径校验/授权/容器语义。
所有方法都直接落到执行后端的同名接口上，因此 Docker/远端失败就是失败，
不会被本地实现替换（本次迁移已删除“元数据缺口回退旧 FileManager”的旁路）。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from config import PROJECT_AGENTS_SKILLS_DIRNAME, WORKSPACE_SKILLS_DIRNAME

logger = logging.getLogger(__name__)

SKILL_FILE_NAME = "SKILL.md"
SKILL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


# ---------------------------------------------------------------------------
# owner → 文件后端
# ---------------------------------------------------------------------------
def _required_owner_attrs() -> tuple:
    """执行后端 owner 必需资源（与 modules.execution_plane.local 保持一致）。"""
    try:
        from modules.execution_plane.local import _REQUIRED_OWNER_ATTRS

        return tuple(_REQUIRED_OWNER_ATTRS)
    except Exception:  # pragma: no cover - 后置安装/重构时的保守兜底
        return (
            "project_path",
            "file_manager",
            "terminal_ops",
            "terminal_manager",
            "background_command_manager",
        )


def _has_execution_resources(owner: Any) -> bool:
    if owner is None:
        return False
    return all(getattr(owner, name, None) is not None for name in _required_owner_attrs())


def _local_files(file_manager: Any) -> Any:
    from modules.execution_plane.files import LocalFileSystem

    return LocalFileSystem(file_manager)


def _local_files_from_manager(file_manager: Any, *, source: str, owner: Any = None) -> "FilesAccess":
    return FilesAccess(files=_local_files(file_manager), owner=owner, source=source)


def _local_files_for_context(context: Any) -> "FilesAccess":
    """无主终端的纯 ContextManager：明确构造本机 FileManager（按路径缓存）。"""
    project_path = getattr(context, "project_path", None)
    if project_path is None:
        raise RuntimeError("ContextManager 没有 project_path，无法装配文件读取后端")
    data_dir = getattr(context, "data_dir", None)
    key = (str(project_path), str(data_dir) if data_dir is not None else "")
    cached = getattr(context, "_execution_local_files", None)
    if isinstance(cached, dict) and cached.get("key") == key:
        access = cached.get("access")
        if access is not None:
            return access

    from modules.file_manager import FileManager

    file_manager = FileManager(
        str(project_path),
        data_dir=str(data_dir) if data_dir is not None else None,
    )
    access = _local_files_from_manager(file_manager, source="context_local", owner=None)
    context._execution_local_files = {"key": key, "access": access}
    return access


def files_for_owner(owner: Any) -> "FilesAccess":
    """解析终端（或任意执行资源 owner）的文件读取入口。

    已绑定后端（含替身/远端）优先级最高，读取失败原样返回；
    只有“从未装配执行资源”的轻量 owner 才走明确的本机适配。
    """
    if owner is None:
        raise ValueError("缺少执行资源 owner，无法解析文件读取后端")

    if getattr(owner, "execution_backend", None) is not None:
        from modules.execution_plane import get_execution_backend

        backend = get_execution_backend(owner)
        return FilesAccess(
            files=backend.files,
            owner=owner,
            source=f"backend:{type(backend).__name__}",
        )

    if _has_execution_resources(owner):
        from modules.execution_plane import get_execution_backend

        backend = get_execution_backend(owner)
        return FilesAccess(
            files=backend.files,
            owner=owner,
            source=f"backend:{type(backend).__name__}",
        )

    file_manager = getattr(owner, "file_manager", None)
    if file_manager is not None:
        logger.debug(
            "owner %s 缺执行资源，按明确的本机文件适配读取（standalone/测试 owner）",
            type(owner).__name__,
        )
        return _local_files_from_manager(
            file_manager, source="local_adapter", owner=owner
        )

    return _local_files_for_context(owner)


def files_for_context(context: Any) -> "FilesAccess":
    """ContextManager 的文件读取入口：自身绑定后端 > 主终端后端 > 明确本机实例。"""
    if getattr(context, "execution_backend", None) is not None:
        return files_for_owner(context)
    terminal = getattr(context, "main_terminal", None)
    if terminal is not None:
        return files_for_owner(terminal)
    return _local_files_for_context(context)


def _execution_owner_of(owner_or_context: Any) -> Any:
    """取出真正持有执行后端的对象（ContextManager 用 main_terminal）。"""
    if owner_or_context is None:
        return None
    terminal = getattr(owner_or_context, "main_terminal", None)
    if terminal is not None:
        return terminal
    return owner_or_context


def execution_environment(owner_or_context: Any) -> Optional[Dict[str, Any]]:
    """执行后端声明的环境（``get_environment``）；未绑定后端时返回 None。

    绑定后端后，平台/路径/模式一律用后端事实（远端即节点事实），
    不得再用控制端 ``platform``/``sys.platform`` 冒充执行目标。
    未能取得时返回 None，由调用方决定降级文案。
    """
    target = _execution_owner_of(owner_or_context)
    if target is None:
        return None
    try:
        if getattr(target, "execution_backend", None) is None and not _has_execution_resources(target):
            return None
        from modules.execution_plane import get_execution_backend

        backend = get_execution_backend(target)
        getter = getattr(backend, "get_environment", None)
        if not callable(getter):
            return None
        env = getter()
        return env if isinstance(env, dict) else None
    except Exception as exc:
        logger.warning("[fs_access] 执行后端环境读取失败: %s", exc)
        return None


def workspace_identity(owner_or_context: Any) -> Optional[str]:
    """执行后端给出的工作区身份（远端=节点路径），拿不到返回 None。"""
    env = execution_environment(owner_or_context)
    if not env:
        return None
    for key in ("workspace_root", "cwd"):
        value = env.get(key)
        if value:
            return str(value)
    return None


# ---------------------------------------------------------------------------
# 读取视图（结果透传，不做任何实现替换）
# ---------------------------------------------------------------------------
class FilesAccess:
    """执行后端文件视图；所有结果原样透传。"""

    def __init__(self, files: Any, *, owner: Any = None, source: str = "backend") -> None:
        self.files = files
        self.owner = owner
        self.source = source

    # -- 内容 ---------------------------------------------------------------
    def read_file(self, path: str) -> Dict[str, Any]:
        return self.files.read_file(path)

    def read_text_segment(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return self.files.read_text_segment(path, **kwargs)

    def search_text(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        return self.files.search_text(path, **kwargs)

    def extract_segments(
        self, path: str, segments: List[Dict[str, Any]], **kwargs: Any
    ) -> Dict[str, Any]:
        return self.files.extract_segments(path, segments, **kwargs)

    def read_binary(self, path: str) -> bytes:
        return self.files.read_binary(path)

    # -- 元数据 -------------------------------------------------------------
    def inspect(self, path: str, access: str = "read") -> Dict[str, Any]:
        """路径元数据（success/path/relative_path/exists/is_file/is_dir/
        within_workspace/suffix/size/mtime）。不存在也返回 success=True。"""
        return self.files.inspect_path(path, access=access)

    def list_dir(self, path: str = "") -> Dict[str, Any]:
        return self.files.list_files(path)


# ---------------------------------------------------------------------------
# 常用读取封装（缺失/不可读 → None，调用方保留原有降级语义）
# ---------------------------------------------------------------------------
def read_text(files_access: FilesAccess, path: str) -> Optional[str]:
    """读取整文件文本；不存在或不可读返回 None（后端异常同样降级并记录）。"""
    try:
        result = files_access.read_file(path)
    except Exception as exc:
        logger.warning("[fs_access] 读取失败 %s: %s", path, exc)
        return None
    if not isinstance(result, dict) or not result.get("success"):
        return None
    content = result.get("content")
    return content if isinstance(content, str) else ""


def inspect_meta(
    files_access: FilesAccess, path: str, access: str = "read"
) -> Dict[str, Any]:
    """元数据封装：异常也归一成 success=False，绝不触碰本机 Path。"""
    try:
        result = files_access.inspect(path, access=access)
    except Exception as exc:
        logger.warning("[fs_access] 元数据读取失败 %s: %s", path, exc)
        return {"success": False, "error": str(exc)}
    return result if isinstance(result, dict) else {"success": False, "error": "invalid metadata"}


def list_dir(files_access: FilesAccess, path: str = "") -> Dict[str, Any]:
    """目录列举封装：异常归一成 success=False。"""
    try:
        result = files_access.list_dir(path)
    except Exception as exc:
        logger.warning("[fs_access] 目录列举失败 %s: %s", path, exc)
        return {"success": False, "error": str(exc)}
    return result if isinstance(result, dict) else {"success": False, "error": "invalid listing"}


def path_exists(files_access: FilesAccess, path: str) -> bool:
    meta = inspect_meta(files_access, path)
    return bool(meta.get("success") and meta.get("exists"))


def path_is_file(files_access: FilesAccess, path: str) -> bool:
    meta = inspect_meta(files_access, path)
    return bool(meta.get("success") and meta.get("is_file"))


def path_is_dir(files_access: FilesAccess, path: str) -> bool:
    meta = inspect_meta(files_access, path)
    return bool(meta.get("success") and meta.get("is_dir"))


def path_mtime(files_access: FilesAccess, path: str) -> Optional[float]:
    meta = inspect_meta(files_access, path)
    mtime = meta.get("mtime") if meta.get("success") and meta.get("exists") else None
    try:
        return float(mtime) if mtime is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 技能目录（读侧遍历经执行后端）
# ---------------------------------------------------------------------------
def _parse_skill_frontmatter(text: str) -> Dict[str, str]:
    """解析简单 YAML 头（与 modules.skills_manager 同一格式，避免语义漂移）。"""
    try:
        from modules.skills_manager import _parse_frontmatter

        return _parse_frontmatter(text)
    except Exception:  # pragma: no cover - skills_manager 结构变更时的保守兜底
        return {}


def _scan_skills_root(
    files_access: FilesAccess, display_dir: str
) -> Optional[List[Dict[str, str]]]:
    """列出一个技能根目录（工作区相对路径）并读取各 SKILL.md 头信息。

    返回 None 表示目录元数据不可用（调用方按失败处理，不回退控制端遍历）；
    目录不存在视为空列表。
    """
    meta = inspect_meta(files_access, display_dir)
    if not meta.get("success"):
        logger.info(
            "[fs_access] 技能目录元数据不可用 %s: %s",
            display_dir,
            meta.get("error"),
        )
        return None
    if not meta.get("exists") or not meta.get("is_dir"):
        return []

    listing = list_dir(files_access, display_dir)
    if not listing.get("success"):
        logger.info(
            "[fs_access] 技能目录无法列举 %s: %s",
            display_dir,
            listing.get("error"),
        )
        return None

    items: List[Dict[str, str]] = []
    folders = listing.get("folders") or []
    names = [str(folder.get("name") or "") for folder in folders]
    for name in sorted(names):
        if not name or not SKILL_ID_PATTERN.match(name):
            continue
        meta_text = read_text(
            files_access, f"{display_dir}/{name}/{SKILL_FILE_NAME}"
        ) or ""
        frontmatter = _parse_skill_frontmatter(meta_text)
        items.append(
            {
                "id": name,
                "label": frontmatter.get("name") or name,
                "description": frontmatter.get("description") or "",
            }
        )
    return items


def build_skills_catalog(
    files_access: FilesAccess, *, scan_project_agents: bool = True
) -> Optional[List[Dict[str, str]]]:
    """经执行后端生成技能目录（id/label/description/display_dir/conflict_dir）。

    返回 None 表示执行后端无法列出目录；调用方按失败处理并告知模型，
    不再回退控制端本机遍历。技能内容读取同样走执行后端。
    """
    merged: Dict[str, Dict[str, str]] = {}
    order: List[str] = []

    workspace_items = _scan_skills_root(files_access, WORKSPACE_SKILLS_DIRNAME)
    if workspace_items is None:
        return None
    for item in workspace_items:
        skill_id = item.get("id")
        if not skill_id:
            continue
        if skill_id not in merged:
            order.append(skill_id)
        entry = dict(item)
        entry["source"] = "workspace"
        entry["display_dir"] = WORKSPACE_SKILLS_DIRNAME
        merged[skill_id] = entry

    if scan_project_agents:
        agents_items = _scan_skills_root(files_access, PROJECT_AGENTS_SKILLS_DIRNAME)
        if agents_items is None:
            return None
        for item in agents_items:
            skill_id = item.get("id")
            if not skill_id:
                continue
            if skill_id in merged:
                merged[skill_id]["conflict_dir"] = PROJECT_AGENTS_SKILLS_DIRNAME
                logger.warning(
                    "[skills] 技能 %s 同时存在于 %s 与 %s，已标记同名冲突",
                    skill_id,
                    WORKSPACE_SKILLS_DIRNAME,
                    PROJECT_AGENTS_SKILLS_DIRNAME,
                )
                continue
            entry = dict(item)
            entry["source"] = "agents"
            entry["display_dir"] = PROJECT_AGENTS_SKILLS_DIRNAME
            order.append(skill_id)
            merged[skill_id] = entry

    return [merged[skill_id] for skill_id in order if skill_id in merged]


__all__ = [
    "FilesAccess",
    "files_for_owner",
    "files_for_context",
    "execution_environment",
    "workspace_identity",
    "read_text",
    "inspect_meta",
    "list_dir",
    "path_exists",
    "path_is_file",
    "path_is_dir",
    "path_mtime",
    "build_skills_catalog",
]
