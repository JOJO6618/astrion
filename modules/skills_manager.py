"""Agent skills manager / 智能体技能管理器。

负责扫描全局 skills 库、生成可用清单，并同步到工作区 skills/。
"""

from __future__ import annotations

import re
import shutil
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from config import AGENT_SKILLS_DIR, CUSTOM_SKILLS_DIR, IS_HOST_MODE, WORKSPACE_SKILLS_DIRNAME
from utils.logger import setup_logger

logger = setup_logger(__name__)

SKILL_FILE_NAME = "SKILL.md"
SKILL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SYNC_LOCK_GUARD = threading.Lock()
_SYNC_LOCKS: Dict[str, threading.Lock] = {}


def _get_sync_lock(skills_dir: Path) -> threading.Lock:
    key = str(skills_dir.resolve())
    with _SYNC_LOCK_GUARD:
        lock = _SYNC_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SYNC_LOCKS[key] = lock
        return lock


def wait_skill_file_ready(
    skill_file: str | Path,
    skills_dir: str | Path,
    *,
    max_wait_seconds: float = 2.5,
    poll_interval: float = 0.2,
) -> bool:
    """Wait until a workspace skill file exists / 等待工作区 skill 文件就绪。

    工作区 skills 目录可能被并发的全量同步（rmtree + 重建）短暂清空，
    读取方在窗口期会看到「文件不存在」。这里先等当前这一轮同步结束
    （复用同步锁做有界等待），再以固定间隔轮询到预算耗尽，避免把瞬时
    缺失误判为硬错误。

    返回 True 表示文件已存在；超过预算仍不存在返回 False。
    """
    target = Path(skill_file)
    if target.is_file():
        return True
    try:
        lock: Optional[threading.Lock] = _get_sync_lock(Path(skills_dir))
    except Exception:
        lock = None
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    while True:
        if lock is not None:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # 锁空闲时会立即拿到并释放，开销可忽略；
                # 锁被同步占用则有界等到本轮重建完成。
                acquired = lock.acquire(timeout=remaining)
                if acquired:
                    lock.release()
            except Exception:
                pass
        if target.is_file():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))
    return target.is_file()


def ensure_agent_skills_dir(base_dir: Optional[str] = None) -> Path:
    """Ensure the global skills directory exists / 确保全局技能目录存在。"""
    root = Path(base_dir or AGENT_SKILLS_DIR).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_workspace_skills_dir(project_path: str | Path) -> Path:
    """Ensure workspace skills directory exists / 确保工作区 skills 目录存在。"""
    root = Path(project_path).expanduser().resolve()
    skills_dir = (root / WORKSPACE_SKILLS_DIRNAME).resolve()
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir


def infer_private_skills_dir(data_dir: str | Path | None) -> Optional[Path]:
    """Infer per-user private skills dir from workspace data_dir.

    host 模式下统一使用运行态根下的 ``agentskills``，不再按用户拆分。
    """
    if not data_dir:
        return None
    try:
        if IS_HOST_MODE:
            return Path(CUSTOM_SKILLS_DIR).expanduser().resolve()
        data_path = Path(data_dir).expanduser().resolve()
        if data_path.name == "data" and data_path.parent.parent.name == "workspaces":
            return (data_path.parent.parent.parent / "agentskills").resolve()
        # Docker Web 项目化布局：
        # users/<user>/projects/<project_id>/data -> users/<user>/personal/agentskills
        if data_path.name == "data" and data_path.parent.parent.name == "projects":
            user_root = data_path.parent.parent.parent
            shared_root = (user_root / "personal" / "agentskills").resolve()
            legacy_root = (data_path.parent / "agentskills").resolve()
            if legacy_root.exists() and legacy_root.is_dir() and legacy_root != shared_root:
                shared_root.mkdir(parents=True, exist_ok=True)
                for item in list(legacy_root.iterdir()):
                    dest = shared_root / item.name
                    if not dest.exists():
                        shutil.move(str(item), str(dest))
                try:
                    legacy_root.rmdir()
                except Exception:
                    pass
            return shared_root
        # 兼容上一轮临时结构：
        # users/<user>/project/<project_id>/data -> users/<user>/personal/agentskills
        if data_path.name == "data" and data_path.parent.parent.name == "project":
            user_root = data_path.parent.parent.parent
            shared_root = (user_root / "personal" / "agentskills").resolve()
            temp_shared_root = (data_path.parent.parent / "shared" / "agentskills").resolve()
            for legacy_root in (temp_shared_root, data_path.parent / "agentskills"):
                if legacy_root.exists() and legacy_root.is_dir() and legacy_root != shared_root:
                    shared_root.mkdir(parents=True, exist_ok=True)
                    for item in list(legacy_root.iterdir()):
                        dest = shared_root / item.name
                        if not dest.exists():
                            shutil.move(str(item), str(dest))
                    try:
                        legacy_root.rmdir()
                    except Exception:
                        pass
            return shared_root
        return (data_path.parent / "agentskills").resolve()
    except Exception:
        return None


def validate_skill_directory(source_dir: str | Path) -> Dict[str, object]:
    """Validate a skill folder with minimal required SKILL.md frontmatter."""
    source = Path(source_dir).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        return {"success": False, "error": "source_dir 不是目录", "source_dir": str(source)}

    skill_id = source.name
    if not _is_valid_skill_id(skill_id):
        return {"success": False, "error": "skill 文件夹名称不合法", "skill_name": skill_id}

    skill_file = source / SKILL_FILE_NAME
    if not skill_file.exists() or not skill_file.is_file():
        return {"success": False, "error": "缺少 SKILL.md", "skill_name": skill_id}

    try:
        content = skill_file.read_text(encoding="utf-8")
    except Exception as exc:
        return {"success": False, "error": f"读取 SKILL.md 失败: {exc}", "skill_name": skill_id}

    if not re.search(r"(?m)^\s*name\s*:", content):
        return {"success": False, "error": "缺少 name:", "skill_name": skill_id}
    if not re.search(r"(?m)^\s*description\s*:", content):
        return {"success": False, "error": "缺少 description:", "skill_name": skill_id}

    return {
        "success": True,
        "skill_name": skill_id,
        "source_dir": str(source),
        "skill_file": str(skill_file),
    }


def archive_skill_directory(source_dir: str | Path, target_root: str | Path) -> Dict[str, object]:
    """Move a validated skill folder into a skills library root."""
    validation = validate_skill_directory(source_dir)
    if not validation.get("success"):
        return validation

    source = Path(str(validation["source_dir"])).expanduser().resolve()
    target_base = Path(target_root).expanduser().resolve()
    target_base.mkdir(parents=True, exist_ok=True)
    target = (target_base / source.name).resolve()

    try:
        target.relative_to(target_base)
    except ValueError:
        return {"success": False, "error": "目标路径不合法", "skill_name": source.name}

    if target.exists():
        return {
            "success": False,
            "error": "目标 skill 已存在",
            "skill_name": source.name,
            "target_dir": str(target),
        }

    try:
        shutil.move(str(source), str(target))
    except Exception as exc:
        return {
            "success": False,
            "error": f"移动 skill 失败: {exc}",
            "skill_name": source.name,
            "source_dir": str(source),
            "target_dir": str(target),
        }

    return {
        "success": True,
        "skill_name": source.name,
        "source_dir": str(source),
        "target_dir": str(target),
    }


def _parse_frontmatter(text: str) -> Dict[str, str]:
    """Parse simple YAML frontmatter / 解析简单 YAML 头信息。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        return {}
    meta: Dict[str, str] = {}
    for line in lines[1:end_idx]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            meta[key] = value
    return meta


def _is_valid_skill_id(name: str) -> bool:
    """Validate skill id / 校验技能目录名。"""
    return bool(name and SKILL_ID_PATTERN.match(name))


def _scan_skills_catalog(root: Path) -> List[Dict[str, str]]:
    """Scan one skills root."""
    if not root.exists() or not root.is_dir():
        return []
    catalog: List[Dict[str, str]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not _is_valid_skill_id(child.name):
            continue
        skill_file = child / SKILL_FILE_NAME
        if not skill_file.exists():
            continue
        meta: Dict[str, str] = {}
        try:
            meta = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        label = meta.get("name") or child.name
        description = meta.get("description") or ""
        catalog.append({
            "id": child.name,
            "label": label,
            "description": description,
        })
    return catalog


def get_skills_catalog(
    base_dir: Optional[str] = None,
    private_dir: Optional[str | Path] = None,
) -> List[Dict[str, str]]:
    """List available skills from global library plus optional private library."""
    roots: List[Path] = [Path(base_dir or AGENT_SKILLS_DIR).expanduser().resolve()]
    if private_dir:
        private_root = Path(private_dir).expanduser().resolve()
        if private_root not in roots:
            roots.append(private_root)

    merged: Dict[str, Dict[str, str]] = {}
    order: List[str] = []
    for root in roots:
        for item in _scan_skills_catalog(root):
            skill_id = item.get("id")
            if not skill_id:
                continue
            if skill_id not in merged:
                order.append(skill_id)
            # Later roots (private) override metadata for same id.
            merged[skill_id] = item
    return [merged[skill_id] for skill_id in order if skill_id in merged]


def _get_skill_source_map(
    base_dir: Optional[str] = None,
    private_dir: Optional[str | Path] = None,
) -> Dict[str, Path]:
    roots: List[Path] = [Path(base_dir or AGENT_SKILLS_DIR).expanduser().resolve()]
    if private_dir:
        private_root = Path(private_dir).expanduser().resolve()
        if private_root not in roots:
            roots.append(private_root)

    sources: Dict[str, Path] = {}
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for item in _scan_skills_catalog(root):
            skill_id = item.get("id")
            if skill_id:
                sources[skill_id] = root / skill_id
    return sources


def resolve_enabled_skills(
    enabled_skills: Optional[Sequence[str]],
    catalog: Sequence[Dict[str, str]],
) -> List[str]:
    """Resolve enabled skills list / 解析启用技能列表。"""
    catalog_ids = [item.get("id", "") for item in catalog if item.get("id")]
    if enabled_skills is None:
        return catalog_ids
    if not isinstance(enabled_skills, (list, tuple)):
        return catalog_ids
    allowed = set(catalog_ids)
    seen = set()
    resolved: List[str] = []
    for item in enabled_skills:
        if not isinstance(item, str):
            continue
        skill_id = item.strip()
        if not skill_id or skill_id in seen or skill_id not in allowed:
            continue
        resolved.append(skill_id)
        seen.add(skill_id)
    return resolved


def build_skills_list(
    catalog: Sequence[Dict[str, str]],
    enabled_skill_ids: Sequence[str],
) -> List[str]:
    """Build skills list lines / 生成 skills 列表行。"""
    if not enabled_skill_ids:
        return []
    lookup = {item.get("id"): item for item in catalog if item.get("id")}
    lines: List[str] = []
    for skill_id in enabled_skill_ids:
        meta = lookup.get(skill_id)
        if not meta:
            continue
        description = (meta.get("description") or "").strip()
        if description:
            lines.append(f".astrion/skills/{skill_id}：{description}")
        else:
            lines.append(f".astrion/skills/{skill_id}")
    return lines


def merge_enabled_skills(
    enabled_skill_ids: Optional[Sequence[str]],
    catalog: Sequence[Dict[str, str]],
    catalog_snapshot: Optional[Sequence[str]] = None,
) -> List[str]:
    """Merge enabled skills with new catalog items / 合并启用列表并默认开启新增技能。"""
    catalog_ids = [item.get("id") for item in catalog if item.get("id")]
    if enabled_skill_ids is None:
        base = list(catalog_ids)
    else:
        base_set = {item for item in enabled_skill_ids if isinstance(item, str)}
        base = [item for item in catalog_ids if item in base_set]
    if catalog_snapshot:
        snapshot_set = {item for item in catalog_snapshot if isinstance(item, str)}
        new_items = [item for item in catalog_ids if item not in snapshot_set]
        for item in new_items:
            if item not in base:
                base.append(item)
    return base


def build_skills_prompt(template: str, skills_list: Sequence[str]) -> str:
    """Build skills prompt from template / 根据模板生成 skills 提示。"""
    if not template:
        return ""
    list_block = "\n".join(skills_list) if skills_list else ""
    content = template
    if list_block:
        content = content.replace("{skills_list}", list_block)
        content = re.sub(r"\[skills_empty\].*?\[/skills_empty\]\n?", "", content, flags=re.S)
    else:
        content = content.replace("{skills_list}", "")
        content = content.replace("[skills_empty]", "").replace("[/skills_empty]", "")
    return content.strip()


def sync_workspace_skills(
    project_path: str | Path,
    enabled_skills: Optional[Sequence[str]] = None,
    base_dir: Optional[str] = None,
    private_dir: Optional[str | Path] = None,
) -> Dict[str, object]:
    """Sync global skills into workspace / 将全局 skills 同步到工作区。"""
    root = Path(project_path).expanduser().resolve()
    skills_dir = (root / WORKSPACE_SKILLS_DIRNAME).resolve()
    try:
        skills_dir.relative_to(root)
    except Exception:
        return {"success": False, "error": "skills 目录不在项目路径内"}

    ensure_agent_skills_dir(base_dir)
    catalog = get_skills_catalog(base_dir, private_dir=private_dir)
    resolved = resolve_enabled_skills(enabled_skills, catalog)
    sources = _get_skill_source_map(base_dir, private_dir=private_dir)

    lock = _get_sync_lock(skills_dir)
    try:
        with lock:
            if skills_dir.exists():
                shutil.rmtree(skills_dir, ignore_errors=True)
            skills_dir.mkdir(parents=True, exist_ok=True)
            for skill_id in resolved:
                src = sources.get(skill_id)
                if src is None or not src.exists() or not src.is_dir():
                    continue
                dst = skills_dir / skill_id
                if dst.exists() and not dst.is_dir():
                    dst.unlink(missing_ok=True)
                shutil.copytree(src, dst, dirs_exist_ok=True)
            return {
                "success": True,
                "copied": list(resolved),
                "available": [item.get("id") for item in catalog],
                "target": str(skills_dir),
            }
    except Exception as exc:
        logger.error("同步 skills 失败: %s", exc, exc_info=True)
        return {"success": False, "error": str(exc), "target": str(skills_dir)}
