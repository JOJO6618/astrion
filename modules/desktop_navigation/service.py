"""Single writer for desktop tabs, independent of renderer document lifetime."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import time
import re
from typing import Callable

from utils.atomic_io import atomic_write_json
from .model import (MAX_TABS, conversation_id, find_tab, make_tab, migrate,
                    navigate, public_state, remove_tabs)


class NavigationConflict(ValueError):
    """A command no longer owns the target or renderer document."""


class DesktopNavigation:
    def __init__(self, path: str):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.checked: dict[str, float] = {}

    def _load(self) -> dict:
        try:
            with self.path.open(encoding="utf-8") as stream:
                data = json.load(stream)
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError("Invalid desktop navigation file")
        return data

    def _save(self, all_states: dict, username: str, state: dict) -> None:
        state["revision"] += 1
        all_states[username] = state
        atomic_write_json(str(self.path), all_states)

    def _state(self, all_states: dict, username: str) -> dict:
        return migrate(all_states.get(username))

    def snapshot(self, username: str, lookup: Callable | None = None) -> dict:
        with self.lock:
            data = self._load()
            state = self._state(data, username)
            before = deepcopy(state)
            if lookup and time.monotonic() - self.checked.get(username, float('-inf')) >= 5:
                self._reconcile(state, lookup)
                self.checked[username] = time.monotonic()
            if data.get(username) != state or state != before:
                self._save(data, username, state)
            return public_state(state)

    @staticmethod
    def _reconcile(state: dict, lookup: Callable) -> None:
        missing = set()
        for tab in state["tabs"]:
            if not tab["workspace_id"] and tab.get("conversation_kind") != "chat":
                tab["error"] = "workspace_unavailable"
                continue
            try:
                info = lookup(tab["workspace_id"], tab["conversation_id"])
            except Exception:
                tab["error"] = "workspace_unavailable"
                continue
            if info.get("exists") is False:
                missing.add(tab["key"])
            else:
                if info.get("label"):
                    tab["workspace_label"] = info["label"]
                if tab.get("error") == "workspace_unavailable":
                    tab["error"] = ""
        remove_tabs(state, missing)

    def command(self, username: str, command_id: str, action: str, payload: dict,
                *, lookup: Callable | None = None) -> dict:
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 100:
            raise ValueError("Missing command identity")
        if not isinstance(payload, dict):
            raise ValueError("Invalid navigation command")
        fingerprint = hashlib.sha256(json.dumps(
            [action, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            data = self._load()
            state = self._state(data, username)
            receipt = next((item for item in state["receipts"] if item["id"] == command_id), None)
            if receipt:
                if receipt["fingerprint"] != fingerprint:
                    raise NavigationConflict("Command identity has already been used")
                return {"state": public_state(state), "result": receipt["result"]}
            result = self._apply(state, action, payload, lookup)
            state["receipts"] = (state["receipts"] + [{
                "id": command_id, "fingerprint": fingerprint, "result": result,
            }])[-256:]
            self._save(data, username, state)
            return {"state": public_state(state), "result": result}

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
            raise ValueError("A workspace is required")
        return workspace, str(info.get("label") or payload.get("workspace_label") or "")

    def _open(self, state: dict, payload: dict, lookup: Callable | None) -> dict:
        workspace, label = self._workspace(payload, state, lookup)
        conv = conversation_id(payload.get("conversation_id"))
        existing = next((tab for tab in state["tabs"] if conv and
                         (tab["workspace_id"], tab["conversation_id"]) == (workspace, conv)), None)
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
        tab = make_tab(workspace, key=key, conv_id=conv,
                       title=info.get("title") or payload.get("title"), label=label,
                       conversation_kind=payload.get("conversation_kind", "agent"))
        if info.get("exists") is False:
            raise NavigationConflict("Conversation no longer exists")
        state["tabs"].append(tab)
        return tab

    @staticmethod
    def _owns(state: dict, payload: dict) -> dict:
        nav = state.get("navigation") or {}
        if (not payload.get("renderer_id") or
                state["renderer_id"] != payload.get("renderer_id") or
                nav.get("id") != payload.get("navigation_id")):
            raise NavigationConflict("Navigation has been superseded")
        return nav

    def _apply(self, state: dict, action: str, payload: dict, lookup: Callable | None) -> dict:
        if action in {"create", "open", "workspace"}:
            if action == "create":
                payload = {**payload, "conversation_id": ""}
            if action == "workspace":
                workspace, _label = self._workspace(payload, state, lookup)
                info = lookup(workspace, "") if lookup else {}
                payload = {**payload, "conversation_id": info.get("active_conversation_id") or ""}
            tab = self._open(state, payload, lookup)
            navigate(state, tab["key"])
            return {"key": tab["key"]}
        if action == "activate":
            tab = find_tab(state, str(payload.get("key") or ""))
            if not tab:
                raise NavigationConflict("Tab is closed")
            navigate(state, tab["key"])
            return {"key": tab["key"]}
        if action == "close":
            remove_tabs(state, {str(payload.get("key") or "")})
            return {}
        if action == "deleted":
            workspace = str(payload.get("workspace_id") or "")
            conv = conversation_id(payload.get("conversation_id"))
            remove_tabs(state, {tab["key"] for tab in state["tabs"] if
                                (tab["workspace_id"], tab["conversation_id"]) == (workspace, conv)})
            return {}
        if action == "bind":
            tab = find_tab(state, str(payload.get("key") or ""))
            # A late creation may keep its real conversation, but cannot recreate a closed tab.
            if not tab:
                return {"bound": False}
            if (tab["workspace_id"] != payload.get("workspace_id") or
                    state["renderer_id"] != payload.get("renderer_id")):
                raise NavigationConflict("Tab owner has changed")
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
        if action == "page":
            page = str(payload.get("page") or "")
            path = page.split("?", 1)[0]
            if not (path in {"/settings", "/git", "/workflows", "/workflow"} or
                    path.startswith(("/settings/", "/workflow/"))):
                raise ValueError("Invalid desktop page")
            state["resume_key"] = state["active_key"] or state["resume_key"]
            state["active_key"] = ""
            navigate(state, page=page)
            return {}
        if action == "resume":
            tab = find_tab(state, state["resume_key"])
            if tab is None:
                tab = self._open(state, payload, lookup)
            navigate(state, tab["key"])
            return {"key": tab["key"]}
        if action == "attach":
            renderer = str(payload.get("renderer_id") or "")
            if not renderer:
                raise ValueError("Missing renderer identity")
            state["renderer_id"] = renderer
            nav = state.get("navigation") or {}
            unchanged = nav.get("id", "") == str(payload.get("seen_navigation_id") or "")
            page = str(payload.get("page") or "")
            pending = nav.get("status") == "pending"
            if unchanged and not pending and page:
                self._apply(state, "page", {"page": page}, lookup)
                state["navigation"]["status"] = "committed"
            elif unchanged and not pending and (payload.get("conversation_id") or
                                                 payload.get("conversation_kind") == "chat"):
                tab = find_tab(state, nav.get("key", ""))
                if not (not payload.get("conversation_id") and tab and tab["kind"] == "new"
                        and tab.get("conversation_kind") == "chat"):
                    tab = self._open(state, payload, lookup)
                navigate(state, tab["key"])
                self._commit(state, state["navigation"])
            elif nav.get("key") and find_tab(state, nav["key"]):
                # Reloads must reacquire the selected document even after a previous commit.
                navigate(state, nav["key"])
            elif nav.get("page"):
                navigate(state, page=nav["page"])
            else:
                tab = find_tab(state, state["active_key"]) or find_tab(state, state["resume_key"])
                if tab is None:
                    tab = state["tabs"][0] if state["tabs"] else self._open(state, payload, lookup)
                navigate(state, tab["key"])
            return {}
        if action == "commit":
            nav = self._owns(state, payload)
            self._commit(state, nav)
            return {}
        if action == "fail":
            nav = self._owns(state, payload)
            tab = find_tab(state, nav.get("key", ""))
            if tab and tab["kind"] == "conv" and lookup:
                try:
                    missing = lookup(tab["workspace_id"], tab["conversation_id"]).get("exists") is False
                except Exception:
                    missing = False
                if missing:
                    remove_tabs(state, {tab["key"]})
                    return {"removed": True}
            nav.update(status="error", error=str(payload.get("error") or "load_failed")[:500])
            if tab:
                tab["error"] = nav["error"]
            return {}
        if action == "title":
            conv = conversation_id(payload.get("conversation_id"))
            for tab in state["tabs"]:
                if (tab["workspace_id"], tab["conversation_id"]) == (payload.get("workspace_id"), conv):
                    tab["title"] = str(payload.get("title") or "")[:200]
            return {}
        raise ValueError("Unknown desktop navigation command")

    @staticmethod
    def _commit(state: dict, nav: dict) -> None:
        if nav.get("status") == "error":
            raise NavigationConflict("Navigation has failed; activate the target to retry")
        tab = find_tab(state, nav.get("key", ""))
        if not nav.get("page") and tab is None:
            raise NavigationConflict("Tab is closed")
        nav.update(status="committed", error="")
        state["active_key"] = tab["key"] if tab else ""
        if tab:
            if tab.get("conversation_kind") != "chat":
                state["workspace_id"] = tab["workspace_id"]
            state["resume_key"] = tab["key"]
            tab["error"] = ""

    def relocated(self, username: str, source_workspace_id: str,
                  target_workspace_id: str, conv_id: str, label: str = "") -> dict:
        """Server-only relocation; preserve tab identity and revoke stale loading."""
        with self.lock:
            data = self._load()
            state = self._state(data, username)
            moved = set()
            for tab in state["tabs"]:
                if (tab.get("conversation_kind", "agent") == "agent"
                        and tab["workspace_id"] == source_workspace_id
                        and tab["conversation_id"] == conv_id):
                    tab.update(workspace_id=target_workspace_id,
                               workspace_label=label, error="")
                    moved.add(tab["key"])
            if moved:
                nav = state["navigation"]
                if nav.get("key") in moved and not nav.get("page"):
                    navigate(state, nav["key"])
                elif state["active_key"] in moved and not nav.get("page") and not nav.get("key"):
                    navigate(state, state["active_key"])
                self._save(data, username, state)
            return public_state(state)

    def deleted(self, username: str, workspace_id: str, conv_id: str) -> dict:
        from uuid import uuid4
        return self.command(username, uuid4().hex, "deleted", {
            "workspace_id": workspace_id, "conversation_id": conv_id,
        })["state"]
