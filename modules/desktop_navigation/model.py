"""Desktop tab identity and persisted session schema v3 (no runtime resources)."""
from __future__ import annotations

from copy import deepcopy
import re
from uuid import uuid4

MAX_TABS = 100
PAGE_TYPES = {"settings", "workflows", "git"}
_KEY_RE = re.compile(r"[A-Za-z0-9_-]{1,100}")
_TAB_STR_FIELDS = ("conversation_id", "workspace_id", "workspace_label", "title", "error")


def new_key() -> str:
    return f"tab_{uuid4().hex}"


def conversation_id(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,180}", text):
        raise ValueError("Invalid conversation identity")
    return text if text.startswith("conv_") else f"conv_{text}"


def make_tab(workspace_id: str, *, key: str = "", conv_id: str = "",
             title: str = "", label: str = "", conversation_kind: str = "agent") -> dict:
    return {
        "conversation_kind": conversation_kind,
        "key": key or new_key(), "kind": "conv" if conv_id else "new",
        "conversation_id": conversation_id(conv_id), "workspace_id": workspace_id,
        "workspace_label": label, "title": str(title or "")[:200], "error": "",
        "placement": "main",
    }


def make_page_tab(page_type: str, route: str, *, workspace_id: str = "", label: str = "",
                  opener_key: str = "", key: str = "") -> dict:
    """独立页面标签：不带 conversation_kind，常驻主窗口，可排序不可拖出。"""
    return {
        "key": key or new_key(), "kind": "page", "page_type": page_type,
        "route": route, "opener_key": opener_key, "conversation_id": "",
        "workspace_id": workspace_id, "workspace_label": label, "title": "",
        "error": "", "placement": "main",
    }


def empty_state() -> dict:
    return {
        "version": 3, "revision": 0, "tabs": [], "active_key": "",
        "workspace_id": "", "renderers": {}, "receipts": [], "closed_keys": [],
    }


def _check_envelope(raw: dict, strings: tuple[str, ...]) -> None:
    if (type(raw.get("revision")) is not int or raw["revision"] < 0 or
            not isinstance(raw.get("receipts"), list) or not isinstance(raw.get("closed_keys"), list) or
            any(not isinstance(raw.get(key), str) for key in strings)):
        raise ValueError("Invalid desktop navigation state")


def _check_tab(tab, keys: set, *, v3: bool) -> None:
    kinds = {"conv", "new", "page"} if v3 else {"conv", "new"}
    if (not isinstance(tab, dict) or not isinstance(tab.get("key"), str) or
            not _KEY_RE.fullmatch(tab["key"]) or tab["key"] in keys or tab.get("kind") not in kinds or
            any(not isinstance(tab.get(key), str) for key in _TAB_STR_FIELDS)):
        raise ValueError("Invalid desktop tab")
    if tab["kind"] == "page":
        if (tab.get("page_type") not in PAGE_TYPES or tab.get("placement") != "main" or
                any(not isinstance(tab.get(key), str) for key in ("route", "opener_key")) or
                (tab["page_type"] == "git" and not tab["workspace_id"])):
            raise ValueError("Invalid desktop page tab")
    else:
        if (tab.get("conversation_kind", "agent") not in {"chat", "agent"} or
                (tab.get("conversation_kind") == "chat" and bool(tab.get("workspace_id")))):
            raise ValueError("Invalid desktop tab")
        if v3 and (tab.get("conversation_kind") not in {"chat", "agent"} or
                   tab.get("placement") not in {"main", "detached"} or
                   (tab["placement"] == "detached" and tab["kind"] == "page")):
            raise ValueError("Invalid desktop tab")
    keys.add(tab["key"])


def _migrate_v2(raw: dict) -> dict:
    required = ("version", "revision", "tabs", "active_key", "resume_key", "workspace_id",
                "renderer_id", "navigation", "receipts", "closed_keys")
    if any(key not in raw for key in required):
        raise ValueError("Incomplete desktop navigation state")
    _check_envelope(raw, ("active_key", "resume_key", "workspace_id", "renderer_id"))
    keys: set = set()
    for tab in raw["tabs"]:
        _check_tab(tab, keys, v3=False)
    nav = raw["navigation"]
    if nav is not None and (not isinstance(nav, dict) or
            any(not isinstance(nav.get(key), str) for key in ("id", "key", "page", "error")) or
            nav.get("status") not in {"pending", "committed", "error"}):
        raise ValueError("Invalid desktop navigation target")
    result = empty_state()
    result.update(revision=raw["revision"], workspace_id=raw["workspace_id"],
                  receipts=deepcopy(raw["receipts"]), closed_keys=deepcopy(raw["closed_keys"]))
    for tab in raw["tabs"]:
        item = deepcopy(tab)
        item.setdefault("conversation_kind", "agent")
        item["placement"] = "main"
        result["tabs"].append(item)
    result["active_key"] = next((key for key in (raw["active_key"], raw["resume_key"])
                                 if key in keys), result["tabs"][0]["key"] if result["tabs"] else "")
    return result


def _validate_v3(raw: dict) -> dict:
    required = tuple(empty_state())
    if any(key not in raw for key in required):
        raise ValueError("Incomplete desktop navigation state")
    _check_envelope(raw, ("active_key", "workspace_id"))
    keys: set = set()
    for tab in raw["tabs"]:
        _check_tab(tab, keys, v3=True)
    renderers = raw["renderers"]
    if (not isinstance(renderers, dict) or
            any(not isinstance(k, str) or not isinstance(v, str) for k, v in renderers.items())):
        raise ValueError("Invalid desktop renderers")
    main = {tab["key"] for tab in raw["tabs"] if tab["placement"] == "main"}
    if raw["active_key"] and raw["active_key"] not in main:
        raise ValueError("Invalid desktop active tab")
    return deepcopy(raw)


def _migrate_v1(raw: dict) -> dict:
    result = empty_state()
    mapping, identities = {}, set()
    for item in (raw.get("tabs") or [])[:MAX_TABS]:
        if not isinstance(item, dict) or item.get("kind") not in {"conv", "new"}:
            continue
        workspace = str(item.get("workspace_id") or "")
        try:
            conv = conversation_id(item.get("conversation_id")) if item["kind"] == "conv" else ""
        except ValueError:
            continue
        if item["kind"] == "conv" and not conv:
            continue
        identity = (workspace, conv)
        existing = next((tab for tab in result["tabs"] if conv and
                         (tab["workspace_id"], tab["conversation_id"]) == identity), None)
        if conv and identity in identities:
            mapping[str(item.get("key") or "")] = existing["key"]
            continue
        tab = make_tab(workspace, conv_id=conv, title=item.get("title"),
                       label=str(item.get("workspace_label") or ""))
        result["tabs"].append(tab)
        mapping[str(item.get("key") or "")] = tab["key"]
        identities.add(identity)
    result["active_key"] = mapping.get(str(raw.get("active_key") or ""), "")
    return result


def migrate(raw) -> dict:
    """Migrate once to v3, keeping order and remapping the old selected tab."""
    if raw is None:
        return empty_state()
    if not isinstance(raw, dict) or not isinstance(raw.get("tabs"), list):
        raise ValueError("Invalid desktop navigation state")
    version = raw.get("version")
    if version == 3:
        return _validate_v3(raw)
    if version == 2:
        return _migrate_v2(raw)
    if version is not None:
        raise ValueError("Unsupported desktop navigation version")
    return _migrate_v1(raw)


def find_tab(state: dict, key: str):
    return next((tab for tab in state["tabs"] if tab["key"] == key), None)


def main_tabs(state: dict) -> list[dict]:
    return [tab for tab in state["tabs"] if tab["placement"] == "main"]


def public_state(state: dict) -> dict:
    return deepcopy({key: value for key, value in state.items()
                     if key not in {"receipts", "closed_keys"}})


def activate(state: dict, tab: dict) -> None:
    """主窗口激活标签；工作区只跟随 Agent 对话/空白标签。"""
    state["active_key"] = tab["key"]
    if tab["kind"] != "page" and tab.get("conversation_kind") != "chat":
        state["workspace_id"] = tab["workspace_id"]


def successor(state: dict, gone: set[str], active: str) -> dict | None:
    """主窗口继任：page 标签优先回 opener，其次右侧相邻、无则左侧（只看 placement=main）。"""
    main = main_tabs(state)
    current = next((tab for tab in main if tab["key"] == active), None)
    if current and current["kind"] == "page":
        opener = next((tab for tab in main if tab["key"] == current["opener_key"]
                       and tab["key"] not in gone), None)
        if opener:
            return opener
    index = next((i for i, tab in enumerate(main) if tab["key"] == active), len(main))
    right = [tab for tab in main[index + 1:] if tab["key"] not in gone]
    left = [tab for tab in reversed(main[:index]) if tab["key"] not in gone]
    return (right or left or [None])[0]


def ensure_main(state: dict, template: dict | None) -> None:
    """主窗口不得为空：补一个空白新对话标签并激活（沿用被移走标签的模式/工作区）。"""
    if main_tabs(state):
        if not find_tab(state, state["active_key"]) or \
                find_tab(state, state["active_key"])["placement"] != "main":
            activate(state, main_tabs(state)[0])
        return
    template = template or {}
    kind = template.get("conversation_kind") or "agent"
    workspace = "" if kind == "chat" else (template.get("workspace_id") or state["workspace_id"])
    tab = make_tab(workspace, label=template.get("workspace_label", ""), conversation_kind=kind)
    state["tabs"].append(tab)
    activate(state, tab)


def remove_tabs(state: dict, keys: set[str], *, ensure_exit: bool = True) -> None:
    tabs = state["tabs"]
    removed = [tab for tab in tabs if tab["key"] in keys]
    if not removed:
        return
    active = state["active_key"]
    pick = successor(state, keys, active) if active in keys else None
    state["tabs"] = [tab for tab in tabs if tab["key"] not in keys]
    state["closed_keys"] = (state["closed_keys"] + [tab["key"] for tab in removed])[-2048:]
    state["renderers"] = {k: v for k, v in state["renderers"].items() if k not in keys}
    if active in keys:
        state["active_key"] = ""
        if pick:
            activate(state, pick)
    if ensure_exit and (active in keys or not main_tabs(state)):
        ensure_main(state, next((tab for tab in removed if tab["kind"] != "page"), removed[0]))
