"""Desktop tab identity and persisted session schema (no runtime resources)."""
from __future__ import annotations

from copy import deepcopy
import re
from uuid import uuid4

MAX_TABS = 100


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
    }


def empty_state() -> dict:
    return {
        "version": 2, "revision": 0, "tabs": [], "active_key": "",
        "resume_key": "", "workspace_id": "", "renderer_id": "",
        "navigation": None, "receipts": [], "closed_keys": [],
    }


def migrate(raw) -> dict:
    """Migrate once, keeping order and remapping the old selected tab."""
    if raw is None:
        return empty_state()
    if not isinstance(raw, dict) or not isinstance(raw.get("tabs"), list):
        raise ValueError("Invalid desktop navigation state")
    if raw.get("version") == 2:
        required = empty_state()
        if any(key not in raw for key in required):
            raise ValueError("Incomplete desktop navigation state")
        if (type(raw["revision"]) is not int or raw["revision"] < 0 or
                not isinstance(raw["receipts"], list) or not isinstance(raw["closed_keys"], list) or
                any(not isinstance(raw[key], str) for key in
                    ("active_key", "resume_key", "workspace_id", "renderer_id"))):
            raise ValueError("Invalid desktop navigation state")
        keys = set()
        for tab in raw["tabs"]:
            if (not isinstance(tab, dict) or not isinstance(tab.get("key"), str) or
                    not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", tab["key"]) or tab["key"] in keys or
                    tab.get("kind") not in {"conv", "new"} or
                    tab.get("conversation_kind", "agent") not in {"chat", "agent"} or
                    (tab.get("conversation_kind") == "chat" and bool(tab.get("workspace_id"))) or
                    any(not isinstance(tab.get(key), str) for key in
                        ("conversation_id", "workspace_id", "workspace_label", "title", "error"))):
                raise ValueError("Invalid desktop tab")
            keys.add(tab["key"])
        nav = raw["navigation"]
        if nav is not None and (not isinstance(nav, dict) or
                any(not isinstance(nav.get(key), str) for key in ("id", "key", "page", "error")) or
                nav.get("status") not in {"pending", "committed", "error"}):
            raise ValueError("Invalid desktop navigation target")
        return deepcopy(raw)
    if raw.get("version") is not None:
        raise ValueError("Unsupported desktop navigation version")
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
    result["resume_key"] = result["active_key"]
    return result


def find_tab(state: dict, key: str):
    return next((tab for tab in state["tabs"] if tab["key"] == key), None)


def public_state(state: dict) -> dict:
    return deepcopy({key: value for key, value in state.items()
                     if key not in {"receipts", "closed_keys"}})


def navigate(state: dict, key: str = "", *, page: str = "") -> None:
    state["navigation"] = {
        "id": uuid4().hex, "key": key, "page": page,
        "status": "pending", "error": "",
    }
    tab = find_tab(state, key)
    if tab:
        tab["error"] = ""


def remove_tabs(state: dict, keys: set[str], *, ensure_exit: bool = True) -> None:
    tabs = state["tabs"]
    removed = [tab for tab in tabs if tab["key"] in keys]
    if not removed:
        return
    index = next(i for i, tab in enumerate(tabs) if tab["key"] in keys)
    state["tabs"] = [tab for tab in tabs if tab["key"] not in keys]
    state["closed_keys"] = (state["closed_keys"] + [tab["key"] for tab in removed])[-2048:]
    if state["resume_key"] in keys:
        state["resume_key"] = ""
    active_removed = state["active_key"] in keys
    if active_removed:
        state["active_key"] = ""
    nav = state.get("navigation") or {}
    target_removed = nav.get("key") in keys
    if not active_removed and not target_removed:
        return
    if nav.get("page"):
        return
    if not state["tabs"] and ensure_exit:
        state["tabs"].append(make_tab(removed[0]["workspace_id"],
                                      label=removed[0]["workspace_label"],
                                      conversation_kind=removed[0].get("conversation_kind", "agent")))
    target = find_tab(state, nav.get("key", "")) or find_tab(state, state["active_key"])
    if not target and state["tabs"]:
        target = state["tabs"][min(index, len(state["tabs"]) - 1)]
    if target:
        navigate(state, target["key"])
    else:
        state["navigation"] = None
