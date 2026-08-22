"""管理员策略配置管理。

职责：
- 持久化管理员在工具分类、模型禁用、前端功能禁用等方面的策略。
- 支持作用域：global / role / user / invite_code，按优先级合并得到最终生效策略。
- 仅在此文件读写配置，避免侵入现有模块。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Any, Tuple

from config.paths import ADMIN_POLICY_FILE
from config.model_profiles import get_registered_model_keys
from modules.custom_tool_registry import CustomToolRegistry, build_default_tool_category
from modules.mcp_server_registry import MCPServerRegistry, build_default_mcp_category

def _allowed_models() -> set[str]:
    return set(get_registered_model_keys(visible_only=False))

# UI 禁用项键名，前后端统一
UI_BLOCK_KEYS = [
    "collapse_workspace",
    "block_file_manager",
    "block_personal_space",
    "block_upload",
    "block_conversation_review",
    "block_tool_toggle",
    "block_realtime_terminal",
    "block_focus_panel",
    "block_token_panel",
    "block_compress_conversation",
    "block_virtual_monitor",
]


def _ensure_file() -> Path:
    path = Path(ADMIN_POLICY_FILE).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _save_policy(_default_policy(), path)
    return path


def _read_policy(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _default_policy()


def _save_policy(payload: Dict[str, Any], path: Path | None = None) -> None:
    target = path or _ensure_file()
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_policy() -> Dict[str, Any]:
    return {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "global": _blank_config(),
        "roles": {},
        "users": {},
        "invites": {},
    }


def _blank_config() -> Dict[str, Any]:
    return {
        "category_overrides": {},   # id -> {label, tools, default_enabled?}
        "remove_categories": [],    # ids
        "forced_category_states": {},  # id -> true/false
        "disabled_models": [],      # list of model keys
        "ui_blocks": {},            # key -> bool
    }


def _merge_config(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """浅合并策略，数组采取并集/覆盖逻辑。"""
    merged = _blank_config()
    base = base or {}
    override = override or {}

    merged["category_overrides"] = {
        **(base.get("category_overrides") or {}),
        **(override.get("category_overrides") or {}),
    }
    merged["remove_categories"] = list({
        *[c for c in base.get("remove_categories") or [] if isinstance(c, str)],
        *[c for c in override.get("remove_categories") or [] if isinstance(c, str)],
    })
    merged["forced_category_states"] = {
        **(base.get("forced_category_states") or {}),
        **(override.get("forced_category_states") or {}),
    }
    merged["disabled_models"] = list({
        *[m for m in base.get("disabled_models") or [] if m in _allowed_models()],
        *[m for m in override.get("disabled_models") or [] if m in _allowed_models()],
    })
    ui_base = base.get("ui_blocks") or {}
    ui_override = override.get("ui_blocks") or {}
    merged["ui_blocks"] = {**ui_base, **ui_override}
    return merged


def load_policy() -> Dict[str, Any]:
    path = _ensure_file()
    payload = _read_policy(path)
    # 补全缺失字段
    if not isinstance(payload, dict):
        payload = _default_policy()
    payload.setdefault("updated_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    payload.setdefault("global", _blank_config())
    payload.setdefault("roles", {})
    payload.setdefault("users", {})
    payload.setdefault("invites", {})
    return payload


def save_scope_policy(target_type: str, target_value: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """更新指定作用域的策略并保存，返回最新策略。"""
    if target_type not in {"global", "role", "user", "invite"}:
        raise ValueError("invalid target_type")

    policy = load_policy()
    normalized = _blank_config()
    normalized = _merge_config(normalized, config or {})

    if target_type == "global":
        policy["global"] = normalized
    elif target_type == "role":
        policy.setdefault("roles", {})[target_value] = normalized
    elif target_type == "user":
        policy.setdefault("users", {})[target_value] = normalized
    else:
        policy.setdefault("invites", {})[target_value] = normalized

    policy["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save_policy(policy)
    return policy


def _collect_categories_with_overrides(overrides: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """从 override 字典生成 {id: {label, tools, default_enabled}}"""
    from core.tool_config import TOOL_CATEGORIES  # 延迟导入避免循环
    registry = CustomToolRegistry()
    mcp_registry = MCPServerRegistry()

    base: Dict[str, Dict[str, Any]] = {
        key: {
            "label": cat.label,
            "tools": list(cat.tools),
            "default_enabled": bool(cat.default_enabled),
            "silent_when_disabled": getattr(cat, "silent_when_disabled", False),
        }
        for key, cat in TOOL_CATEGORIES.items()
    }

    # 注入自定义工具分类（动态）
    custom_cat = build_default_tool_category()
    custom_cat_id = custom_cat.get("id", "custom")
    custom_tools = [item.get("id") for item in registry.list_tools() if item.get("id")]
    base[custom_cat_id] = {
        "label": custom_cat.get("label", "自定义工具"),
        "tools": custom_tools,
        "default_enabled": True,
        "silent_when_disabled": False,
    }

    # 注入 MCP 工具分类（动态）
    mcp_cat = build_default_mcp_category()
    mcp_cat_id = mcp_cat.get("id", "mcp")
    base[mcp_cat_id] = {
        "label": mcp_cat.get("label", "MCP 工具"),
        "tools": ["list_mcp_servers"],
        "default_enabled": True,
        "silent_when_disabled": False,
    }
    for server in mcp_registry.list_enabled_servers():
        sid = str(server.get("id") or "").strip()
        if not sid:
            continue
        cat_id = MCPServerRegistry.make_server_category_id(sid)
        base[cat_id] = {
            "label": MCPServerRegistry.make_server_category_label(server),
            "tools": mcp_registry.build_cached_alias_list(server_id=sid),
            "default_enabled": True,
            "silent_when_disabled": False,
        }

    remove_ids = set(overrides.get("remove_categories") or [])
    for rid in remove_ids:
        base.pop(rid, None)

    for cid, payload in (overrides.get("category_overrides") or {}).items():
        if not isinstance(cid, str):
            continue
        if not isinstance(payload, dict):
            continue
        label = payload.get("label") or cid
        tools = payload.get("tools") or []
        if not isinstance(tools, list):
            continue
        default_enabled = bool(payload.get("default_enabled", True))
        base[cid] = {
            "label": str(label),
            "tools": [t for t in tools if isinstance(t, str)],
            "default_enabled": default_enabled,
            "silent_when_disabled": bool(payload.get("silent_when_disabled", False)),
        }

    # MCP 分类由运行时动态决定：基础 mcp 仅保留原生 list_mcp_servers，
    # 每个 mcp_server__* 分类独立承载该服务别名工具，避免旧策略覆盖造成错乱。
    base["mcp"] = {
        **(base.get("mcp") or {}),
        "label": (base.get("mcp") or {}).get("label") or mcp_cat.get("label", "MCP 工具"),
        "tools": ["list_mcp_servers"],
        "default_enabled": bool((base.get("mcp") or {}).get("default_enabled", True)),
        "silent_when_disabled": bool((base.get("mcp") or {}).get("silent_when_disabled", False)),
    }
    active_server_categories = set()
    for server in mcp_registry.list_enabled_servers():
        sid = str(server.get("id") or "").strip()
        if not sid:
            continue
        cat_id = MCPServerRegistry.make_server_category_id(sid)
        active_server_categories.add(cat_id)
        existing = base.get(cat_id) or {}
        base[cat_id] = {
            "label": MCPServerRegistry.make_server_category_label(server),
            "tools": mcp_registry.build_cached_alias_list(server_id=sid),
            "default_enabled": bool(existing.get("default_enabled", True)),
            "silent_when_disabled": bool(existing.get("silent_when_disabled", False)),
        }
    for cid in list(base.keys()):
        if isinstance(cid, str) and cid.startswith("mcp_server__") and cid not in active_server_categories:
            base.pop(cid, None)
    return base


def get_effective_policy(username: str | None, role: str | None, invite_code: str | None) -> Dict[str, Any]:
    """按优先级合并策略，返回生效配置。"""
    policy = load_policy()
    scopes: Tuple[Tuple[str, Dict[str, Any]], ...] = (
        ("global", policy.get("global") or _blank_config()),
        ("role", (policy.get("roles") or {}).get(role or "", {})),
        ("invite", (policy.get("invites") or {}).get(invite_code or "", {})),
        ("user", (policy.get("users") or {}).get(username or "", {})),
    )

    merged = _blank_config()
    for _, cfg in scopes:
        merged = _merge_config(merged, cfg or {})

    # 计算最终分类
    categories = _collect_categories_with_overrides(merged)
    forced_states = {
        key: bool(val) if isinstance(val, bool) else None
        for key, val in (merged.get("forced_category_states") or {}).items()
        if key
    }
    disabled_models = [m for m in merged.get("disabled_models") or [] if m in _allowed_models()]
    ui_blocks = {k: bool(v) for k, v in (merged.get("ui_blocks") or {}).items() if k in UI_BLOCK_KEYS}

    return {
        "categories": categories,
        "forced_category_states": forced_states,
        "disabled_models": disabled_models,
        "ui_blocks": ui_blocks,
        "updated_at": policy.get("updated_at"),
    }


def describe_defaults() -> Dict[str, Any]:
    """返回默认（未覆盖）工具分类，用于前端渲染。"""
    from core.tool_config import TOOL_CATEGORIES
    registry = CustomToolRegistry()
    mcp_registry = MCPServerRegistry()

    categories = {
        key: {
            "label": cat.label,
            "tools": list(cat.tools),
            "default_enabled": bool(cat.default_enabled),
            "silent_when_disabled": getattr(cat, "silent_when_disabled", False),
        }
        for key, cat in TOOL_CATEGORIES.items()
    }

    # 自定义工具分类
    custom_cat = build_default_tool_category()
    custom_cat_id = custom_cat.get("id", "custom")
    categories[custom_cat_id] = {
        "label": custom_cat.get("label", "自定义工具"),
        "tools": [item.get("id") for item in registry.list_tools() if item.get("id")],
        "default_enabled": True,
        "silent_when_disabled": False,
    }

    # MCP 工具分类
    mcp_cat = build_default_mcp_category()
    mcp_cat_id = mcp_cat.get("id", "mcp")
    categories[mcp_cat_id] = {
        "label": mcp_cat.get("label", "MCP 工具"),
        "tools": ["list_mcp_servers"],
        "default_enabled": True,
        "silent_when_disabled": False,
    }
    for server in mcp_registry.list_enabled_servers():
        sid = str(server.get("id") or "").strip()
        if not sid:
            continue
        cat_id = MCPServerRegistry.make_server_category_id(sid)
        categories[cat_id] = {
            "label": MCPServerRegistry.make_server_category_label(server),
            "tools": mcp_registry.build_cached_alias_list(server_id=sid),
            "default_enabled": True,
            "silent_when_disabled": False,
        }

    return {
        "categories": categories,
        "models": sorted(list(_allowed_models())),
        "ui_block_keys": UI_BLOCK_KEYS,
    }
