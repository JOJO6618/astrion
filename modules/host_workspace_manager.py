"""宿主机模式工作区配置管理。"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from config import HOST_WORKSPACES_FILE
from utils.atomic_io import replace_with_retry

_WORKSPACE_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
_REPO_ROOT = Path(__file__).resolve().parents[1]
_HOST_WORKSPACE_LOCK = threading.RLock()


def _resolve_config_path(config_path: Optional[Union[str, Path]] = None) -> Path:
    raw = str(config_path or HOST_WORKSPACES_FILE or "").strip()
    if not raw:
        raw = "./config/host_workspaces.json"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (_REPO_ROOT / path).resolve()
    return path


def _default_payload() -> Dict[str, Any]:
    """空配置：不在启动时自动创建任何工作区，等待用户手动创建。"""
    return {
        "default_workspace_id": "",
        "workspaces": [],
    }


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        # Windows 瞬时持锁（并发读取/杀软扫描）重试，POSIX 行为不变
        replace_with_retry(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def _ensure_config_file(path: Path, *, strict: bool = False) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
            if strict:
                raise RuntimeError("host_workspaces 配置格式错误（非 JSON 对象），已停止写入以避免覆盖原文件")
        except Exception as exc:
            if strict:
                raise RuntimeError(f"host_workspaces 配置解析失败，已停止写入以避免覆盖原文件: {exc}") from exc
            # 只回退到内存默认值，不覆盖磁盘文件
            return _default_payload()
    data = _default_payload()
    _atomic_write_json(path, data)
    return data


def _normalize_workspace_id(raw: Any, index: int) -> str:
    value = str(raw or "").strip()
    if _WORKSPACE_ID_RE.match(value):
        return value
    return f"workspace_{index + 1}"


def _slugify_workspace_id(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-._")
    if not value:
        return "workspace"
    if _WORKSPACE_ID_RE.match(value):
        return value
    return "workspace"


def _normalize_workspace_path(raw: Any) -> Path:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("工作区路径不能为空")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (_REPO_ROOT / path).resolve()
    else:
        path = path.resolve()
    return path


def _normalize_entry(raw: Any, index: int) -> Optional[Dict[str, str]]:
    if not isinstance(raw, dict):
        return None
    path_raw = str(raw.get("path") or "").strip()
    if not path_raw:
        # 路径为空的条目视为无效，不再回退到源码树下的默认目录
        return None
    workspace_id = _normalize_workspace_id(
        raw.get("workspace_id") or raw.get("id") or raw.get("name"), index
    )
    label = str(raw.get("label") or raw.get("name") or workspace_id).strip() or workspace_id
    path = _normalize_workspace_path(path_raw)
    path.mkdir(parents=True, exist_ok=True)
    return {
        "workspace_id": workspace_id,
        "label": label,
        "path": str(path),
    }


def load_host_workspace_catalog(
    config_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    cfg_path = _resolve_config_path(config_path)
    with _HOST_WORKSPACE_LOCK:
        payload = _ensure_config_file(cfg_path, strict=False)
    raw_workspaces = payload.get("workspaces")
    if not isinstance(raw_workspaces, list):
        raw_workspaces = []

    seen_ids = set()
    workspaces: List[Dict[str, str]] = []
    for idx, item in enumerate(raw_workspaces):
        normalized = _normalize_entry(item, idx)
        if not normalized:
            continue
        ws_id = normalized["workspace_id"]
        if ws_id in seen_ids:
            continue
        seen_ids.add(ws_id)
        workspaces.append(normalized)

    if not workspaces:
        # 列表为空时不再自动创建任何工作区，由用户手动创建
        return {
            "source_path": str(cfg_path),
            "default_workspace_id": "",
            "workspaces": [],
        }

    default_workspace_id = str(payload.get("default_workspace_id") or "").strip()
    if default_workspace_id not in seen_ids:
        default_workspace_id = workspaces[0]["workspace_id"]

    return {
        "source_path": str(cfg_path),
        "default_workspace_id": default_workspace_id,
        "workspaces": workspaces,
    }


def resolve_host_workspace(
    selected_workspace_id: Optional[str] = None,
    config_path: Optional[Union[str, Path]] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, str]]]:
    """解析当前工作区；没有任何工作区时返回 ``(catalog, None)``，由调用方提示用户手动创建。"""
    catalog = load_host_workspace_catalog(config_path=config_path)
    candidates = catalog.get("workspaces") or []
    selected_id = str(selected_workspace_id or "").strip()
    current = None
    if selected_id:
        current = next((ws for ws in candidates if ws.get("workspace_id") == selected_id), None)
    if not current:
        default_id = catalog.get("default_workspace_id")
        current = next((ws for ws in candidates if ws.get("workspace_id") == default_id), None)
    if not current and candidates:
        current = candidates[0]
    return catalog, current


def create_host_workspace(
    path: str,
    label: Optional[str] = None,
    *,
    set_default: bool = False,
    config_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    cfg_path = _resolve_config_path(config_path)
    with _HOST_WORKSPACE_LOCK:
        payload = _ensure_config_file(cfg_path, strict=True)

        raw_workspaces = payload.get("workspaces")
        if not isinstance(raw_workspaces, list):
            raw_workspaces = []

        normalized_path = _normalize_workspace_path(path)
        normalized_path.mkdir(parents=True, exist_ok=True)
        target_path_str = str(normalized_path)

        for idx, item in enumerate(raw_workspaces):
            existing = _normalize_entry(item, idx)
            if not existing:
                continue
            if existing.get("path") == target_path_str:
                # 已存在相同路径则直接返回
                return {
                    "created": False,
                    "workspace": existing,
                    "catalog": load_host_workspace_catalog(config_path=cfg_path),
                }

        clean_label = str(label or "").strip()
        base_id_seed = clean_label or normalized_path.name or "workspace"
        base_id = _slugify_workspace_id(base_id_seed)
        existing_ids = {
            _normalize_workspace_id(item.get("workspace_id") or item.get("id"), i)
            for i, item in enumerate(raw_workspaces)
            if isinstance(item, dict)
        }
        workspace_id = base_id
        suffix = 2
        while workspace_id in existing_ids:
            workspace_id = f"{base_id}-{suffix}"
            suffix += 1

        workspace = {
            "workspace_id": workspace_id,
            "label": clean_label or normalized_path.name or workspace_id,
            "path": target_path_str,
        }
        raw_workspaces.append(workspace)
        payload["workspaces"] = raw_workspaces

        default_id = str(payload.get("default_workspace_id") or "").strip()
        if set_default or not default_id:
            payload["default_workspace_id"] = workspace_id

        _atomic_write_json(cfg_path, payload)

    return {
        "created": True,
        "workspace": workspace,
        "catalog": load_host_workspace_catalog(config_path=cfg_path),
    }


def rename_host_workspace(
    workspace_id: str,
    label: str,
    config_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    ws_id = str(workspace_id or "").strip()
    clean_label = str(label or "").strip()
    if not ws_id:
        raise ValueError("缺少 workspace_id")
    if not clean_label:
        raise ValueError("工作区名称不能为空")

    cfg_path = _resolve_config_path(config_path)
    with _HOST_WORKSPACE_LOCK:
        payload = _ensure_config_file(cfg_path, strict=True)
        raw_workspaces = payload.get("workspaces")
        if not isinstance(raw_workspaces, list):
            raw_workspaces = []
        updated = None
        for idx, item in enumerate(raw_workspaces):
            normalized = _normalize_entry(item, idx)
            if not normalized or normalized.get("workspace_id") != ws_id:
                continue
            item["workspace_id"] = ws_id
            item["label"] = clean_label
            item["path"] = normalized.get("path")
            updated = dict(item)
            break
        if not updated:
            raise ValueError("工作区不存在")
        payload["workspaces"] = raw_workspaces
        _atomic_write_json(cfg_path, payload)

    return {
        "workspace": updated,
        "catalog": load_host_workspace_catalog(config_path=cfg_path),
    }


def delete_host_workspace(
    workspace_id: str,
    config_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    ws_id = str(workspace_id or "").strip()
    if not ws_id:
        raise ValueError("缺少 workspace_id")

    cfg_path = _resolve_config_path(config_path)
    with _HOST_WORKSPACE_LOCK:
        payload = _ensure_config_file(cfg_path, strict=True)
        raw_workspaces = payload.get("workspaces")
        if not isinstance(raw_workspaces, list):
            raw_workspaces = []

        kept = []
        deleted = None
        for idx, item in enumerate(raw_workspaces):
            normalized = _normalize_entry(item, idx)
            if normalized and normalized.get("workspace_id") == ws_id:
                deleted = normalized
                continue
            kept.append(item)
        if not deleted:
            raise ValueError("工作区不存在")
        # 允许删除最后一个工作区：回到「无工作区」状态，等待用户重新手动创建

        payload["workspaces"] = kept
        if str(payload.get("default_workspace_id") or "").strip() == ws_id:
            if kept:
                first = _normalize_entry(kept[0], 0)
                payload["default_workspace_id"] = (first or {}).get("workspace_id") or ""
            else:
                payload["default_workspace_id"] = ""
        _atomic_write_json(cfg_path, payload)

    return {
        "deleted": deleted,
        "catalog": load_host_workspace_catalog(config_path=cfg_path),
    }


def set_default_host_workspace(
    workspace_id: str,
    config_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """设置指定工作区为默认工作区。"""
    ws_id = str(workspace_id or "").strip()
    if not ws_id:
        raise ValueError("缺少 workspace_id")

    cfg_path = _resolve_config_path(config_path)
    with _HOST_WORKSPACE_LOCK:
        payload = _ensure_config_file(cfg_path, strict=True)
        raw_workspaces = payload.get("workspaces")
        if not isinstance(raw_workspaces, list):
            raw_workspaces = []

        seen_ids = set()
        for idx, item in enumerate(raw_workspaces):
            normalized = _normalize_entry(item, idx)
            if normalized:
                seen_ids.add(normalized.get("workspace_id"))

        if ws_id not in seen_ids:
            raise ValueError("工作区不存在")

        payload["default_workspace_id"] = ws_id
        _atomic_write_json(cfg_path, payload)

    return {
        "default_workspace_id": ws_id,
        "catalog": load_host_workspace_catalog(config_path=cfg_path),
    }


__all__ = [
    "load_host_workspace_catalog",
    "resolve_host_workspace",
    "create_host_workspace",
    "set_default_host_workspace",
]
