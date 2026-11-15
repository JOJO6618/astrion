"""v3 命令实现：只改内存状态，持久化与 receipt 由 service 负责。"""
from __future__ import annotations

import re
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from .model import (MAX_TABS, activate, conversation_id, ensure_main, find_tab, main_tabs,
                    make_page_tab, make_tab, remove_tabs, successor)


class NavigationConflict(ValueError):
    """A command no longer owns the target or renderer document."""


class WorkspaceRequired(ValueError):
    """Zero-workspace host install: agent tabs need a workspace, not a failure.

    新装宿主机模式（尚未创建任何工作区）是正常初始空态，调用方据此返回
    code=no_workspace 让前端静默降级，而不是渲染「同步失败」。
    """


def page_type_of(route: str) -> str:
    path = route.split("?", 1)[0]
    if path == "/settings" or path.startswith("/settings/"):
        return "settings"
    if path in {"/workflows", "/workflow"} or path.startswith("/workflow/"):
        return "workflows"
    if path == "/git":
        return "git"
    raise ValueError("Invalid desktop page")


def _need_tab(state: dict, key) -> dict:
    tab = find_tab(state, str(key or ""))
    if not tab:
        raise NavigationConflict("Tab is closed")
    return tab


class CommandsMixin:
    @staticmethod
    def _workspace(payload: dict, state: dict, lookup: Callable | None) -> tuple[str, str]:
        if payload.get("conversation_kind") == "chat":
            if payload.get("workspace_id"):
                raise ValueError("Chat has no workspace")
            return "", ""
        if payload.get("conversation_kind", "agent") != "agent":
            raise ValueError("Invalid conversation kind")
        workspace = str(payload.get("workspace_id") or state.get("workspace_id") or "")
        info = lookup(workspace, "") if lookup else {}
        workspace = str(info.get("workspace_id") or workspace)
        if not workspace:
            raise WorkspaceRequired("A workspace is required")
        return workspace, str(info.get("label") or payload.get("workspace_label") or "")

    def _open(self, state: dict, payload: dict, lookup: Callable | None) -> dict:
        workspace, label = self._workspace(payload, state, lookup)
        conv = conversation_id(payload.get("conversation_id"))
        kind = payload.get("conversation_kind", "agent")
        existing = next((tab for tab in state["tabs"] if conv and tab["kind"] == "conv" and
                         (tab["conversation_kind"], tab["workspace_id"], tab["conversation_id"])
                         == (kind, workspace, conv)), None)
        if existing:
            return existing
        key = str(payload.get("key") or "")
        if key and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key):
            raise ValueError("Invalid tab identity")
        if key and (key in state["closed_keys"] or find_tab(state, key)):
            raise NavigationConflict("Tab identity has already been used")
        if len(state["tabs"]) >= MAX_TABS:
            raise ValueError("Too many open tabs")
        info = lookup(workspace, conv) if conv and lookup else {}
        if info.get("exists") is False:
            raise NavigationConflict("Conversation no longer exists")
        tab = make_tab(workspace, key=key, conv_id=conv,
                       title=info.get("title") or payload.get("title"), label=label,
                       conversation_kind=kind)
        state["tabs"].append(tab)
        return tab

    @staticmethod
    def _show(state: dict, tab: dict) -> dict:
        """open/workspace 的落点：detached 不改 active，只让壳层前置独立窗口。"""
        if tab["placement"] == "detached":
            return {"key": tab["key"], "focus": "detached"}
        activate(state, tab)
        return {"key": tab["key"]}

    def _page(self, state: dict, payload: dict, lookup: Callable | None) -> dict:
        route = str(payload.get("route") or payload.get("page") or "")[:500]
        page_type = page_type_of(route)
        if payload.get("page_type") not in (None, "", page_type):
            raise ValueError("Invalid desktop page")
        workspace = label = ""
        if page_type == "git":
            query = parse_qs(urlsplit(route).query).get("workspace_id", [""])[0]
            workspace = str(query or payload.get("workspace_id") or "")
            if not workspace:
                raise ValueError("Git page requires a workspace")
            try:
                info = lookup(workspace, "") if lookup else {}
            except Exception as exc:
                raise ValueError("Workspace unavailable") from exc
            workspace = str(info.get("workspace_id") or workspace)
            label = str(info.get("label") or workspace)
        tab = next((item for item in state["tabs"] if item["kind"] == "page" and
                    item["page_type"] == page_type and item["workspace_id"] == workspace), None)
        opener = state["active_key"] if state["active_key"] != (tab or {}).get("key") else ""
        if tab is None:
            if len(state["tabs"]) >= MAX_TABS:
                raise ValueError("Too many open tabs")
            tab = make_page_tab(page_type, route, workspace_id=workspace, label=label,
                                opener_key=opener)
            state["tabs"].append(tab)
        else:
            tab["route"] = route
            if label:
                tab["workspace_label"] = label
            if opener:
                tab["opener_key"] = opener
        activate(state, tab)
        return {"key": tab["key"]}

    def _rebind_workspace(self, state: dict, payload: dict, lookup: Callable | None) -> dict:
        """独立窗口中的空白标签切换工作区：改写该标签自身的工作区，不新开标签。"""
        tab = _need_tab(state, payload.get("key"))
        if tab["kind"] != "new" or tab["placement"] != "detached" or \
                tab.get("conversation_kind") == "chat":
            raise ValueError("Only a blank detached tab can change workspace in place")
        workspace, label = self._workspace({k: v for k, v in payload.items() if k != "key"},
                                           state, lookup)
        tab.update(workspace_id=workspace, workspace_label=label, error="")
        return {"key": tab["key"], "rebound": True}

    def _detach(self, state: dict, key) -> dict:
        tab = _need_tab(state, key)
        # 对话标签与空白新对话标签可拖出（空白标签在独立窗口里写首条消息）；页面标签不可。
        if tab["kind"] not in {"conv", "new"}:
            raise ValueError("Only conversation tabs can be detached")
        if tab["placement"] == "detached":
            return {}
        pick = successor(state, {tab["key"]}, state["active_key"]) \
            if state["active_key"] == tab["key"] else None
        tab["placement"] = "detached"
        if state["active_key"] == tab["key"]:
            state["active_key"] = ""
            if pick:
                activate(state, pick)
        ensure_main(state, tab)
        return {}

    @staticmethod
    def _dock(state: dict, key) -> dict:
        tab = _need_tab(state, key)
        if tab["kind"] == "new" and tab["placement"] == "detached":
            # 关闭仍为空白的独立窗口：没有可丢失的内容，直接移除标签而不是放回主窗口。
            # active 属于 main，不受影响；关闭身份照常记录。
            remove_tabs(state, {tab["key"]})
            return {"removed": True}
        state["tabs"].remove(tab)
        state["tabs"].append(tab)
        tab["placement"] = "main"
        if not state["active_key"]:
            activate(state, tab)
        return {}

    @staticmethod
    def _reorder(state: dict, payload: dict) -> dict:
        tab = _need_tab(state, payload.get("key"))
        if tab["placement"] != "main":
            raise NavigationConflict("Tab is detached")
        before_key = str(payload.get("before_key") or "")
        before = None
        if before_key:
            before = find_tab(state, before_key)
            if before is None or before["placement"] != "main":
                raise NavigationConflict("Reorder target is unavailable")
        if before is tab:
            return {}
        state["tabs"].remove(tab)
        if before is not None:
            state["tabs"].insert(state["tabs"].index(before), tab)
        else:
            last = max((i for i, item in enumerate(state["tabs"]) if item["placement"] == "main"),
                       default=-1)
            state["tabs"].insert(last + 1, tab)
        return {}

    @staticmethod
    def _owns(state: dict, tab: dict, payload: dict) -> None:
        renderer = payload.get("renderer_id")
        if not renderer or state["renderers"].get(tab["key"]) != renderer:
            raise NavigationConflict("Tab owner has changed")

    def _bind(self, state: dict, payload: dict, lookup: Callable | None) -> dict:
        tab = find_tab(state, str(payload.get("key") or ""))
        # A late creation may keep its real conversation, but cannot recreate a closed tab.
        if not tab:
            return {"bound": False}
        if tab["workspace_id"] != payload.get("workspace_id"):
            raise NavigationConflict("Tab owner has changed")
        self._owns(state, tab, payload)
        conv = conversation_id(payload.get("conversation_id"))
        if not conv:
            return {"bound": False}
        if tab["kind"] != "new":
            if tab["conversation_id"] == conv:
                return {"bound": True}
            raise NavigationConflict("Tab is already bound")
        if lookup and lookup(tab["workspace_id"], conv).get("exists") is False:
            raise NavigationConflict("Conversation no longer exists")
        tab.update(kind="conv", conversation_id=conv, title=str(payload.get("title") or "")[:200])
        return {"bound": True}

    def _fail(self, state: dict, payload: dict, lookup: Callable | None) -> dict:
        tab = find_tab(state, str(payload.get("key") or ""))
        if not tab:
            return {}
        self._owns(state, tab, payload)
        if tab["kind"] == "conv" and lookup:
            try:
                missing = lookup(tab["workspace_id"], tab["conversation_id"]).get("exists") is False
            except Exception:
                missing = False
            if missing:
                remove_tabs(state, {tab["key"]})
                return {"removed": True}
        tab["error"] = str(payload.get("error") or "load_failed")[:500]
        return {}

    def _apply(self, state: dict, action: str, payload: dict, lookup: Callable | None) -> dict:
        if action in {"create", "open", "workspace"}:
            if action == "create":
                payload = {**payload, "conversation_id": ""}
            if action == "workspace" and payload.get("key"):
                return self._rebind_workspace(state, payload, lookup)
            if action == "workspace":
                workspace, _label = self._workspace(payload, state, lookup)
                info = lookup(workspace, "") if lookup else {}
                payload = {**payload, "conversation_id": info.get("active_conversation_id") or ""}
            return self._show(state, self._open(state, payload, lookup))
        if action == "page":
            return self._page(state, payload, lookup)
        if action == "activate":
            tab = _need_tab(state, payload.get("key"))
            if tab["placement"] != "main":
                raise NavigationConflict("Tab is detached")
            activate(state, tab)
            return {"key": tab["key"]}
        if action == "close":
            remove_tabs(state, {str(payload.get("key") or "")})
            return {}
        if action == "reorder":
            return self._reorder(state, payload)
        if action == "detach":
            return self._detach(state, payload.get("key"))
        if action == "dock":
            return self._dock(state, payload.get("key"))
        if action == "attach":
            tab = _need_tab(state, payload.get("key"))
            renderer = str(payload.get("renderer_id") or "")
            if not renderer or len(renderer) > 200:
                raise ValueError("Missing renderer identity")
            state["renderers"][tab["key"]] = renderer
            tab["error"] = ""
            return {}
        if action == "bind":
            return self._bind(state, payload, lookup)
        if action == "fail":
            return self._fail(state, payload, lookup)
        if action == "deleted":
            workspace = str(payload.get("workspace_id") or "")
            conv = conversation_id(payload.get("conversation_id"))
            if conv:
                remove_tabs(state, {tab["key"] for tab in state["tabs"] if tab["kind"] != "page" and
                                    (tab["workspace_id"], tab["conversation_id"]) == (workspace, conv)})
            return {}
        if action == "title":
            conv = conversation_id(payload.get("conversation_id"))
            for tab in state["tabs"]:
                if conv and tab["kind"] != "page" and \
                        (tab["workspace_id"], tab["conversation_id"]) == (payload.get("workspace_id"), conv):
                    tab["title"] = str(payload.get("title") or "")[:200]
            return {}
        raise ValueError("Unknown desktop navigation command")
