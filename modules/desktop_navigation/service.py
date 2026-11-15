"""Single writer for desktop tabs, independent of renderer document lifetime."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Callable

from utils.atomic_io import atomic_write_json
from .commands import CommandsMixin, NavigationConflict, WorkspaceRequired
from .model import find_tab, migrate, public_state, remove_tabs

__all__ = ["DesktopNavigation", "NavigationConflict", "WorkspaceRequired"]

# 会改变「用户当前注意力」的命令：其结果标签记为最近聚焦（仅内存，供 CrossPet）
_FOCUS_ACTIONS = {"create", "open", "workspace", "activate", "page"}


class DesktopNavigation(CommandsMixin):
    def __init__(self, path: str):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.checked: dict[str, float] = {}
        # 最近聚焦标签：不落盘、不增 revision，仅 CrossPet 使用
        self.focused: dict[str, str] = {}

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
            is_git = tab["kind"] == "page" and tab.get("page_type") == "git"
            if tab["kind"] == "page" and not is_git:
                continue  # 设置/工作流页没有对话或工作区可核验
            if not tab["workspace_id"] and tab.get("conversation_kind") != "chat":
                tab["error"] = "workspace_unavailable"
                continue
            try:
                info = lookup(tab["workspace_id"], tab["conversation_id"])
            except Exception:
                tab["error"] = "workspace_unavailable"
                continue
            if info.get("no_workspace"):
                # 零工作区：标签保留但标为暂不可用，不删除、不抛错
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
            if action == "focus":
                # 只记内存：不写 receipt、不增 revision
                tab = find_tab(state, str(payload.get("key") or ""))
                if not tab:
                    raise NavigationConflict("Tab is closed")
                self.focused[username] = tab["key"]
                return {"state": public_state(state), "result": {"key": tab["key"]}}
            receipt = next((item for item in state["receipts"] if item["id"] == command_id), None)
            if receipt:
                if receipt["fingerprint"] != fingerprint:
                    raise NavigationConflict("Command identity has already been used")
                return {"state": public_state(state), "result": receipt["result"]}
            result = self._apply(state, action, payload, lookup)
            if action in _FOCUS_ACTIONS and result.get("key"):
                self.focused[username] = result["key"]
            state["receipts"] = (state["receipts"] + [{
                "id": command_id, "fingerprint": fingerprint, "result": result,
            }])[-256:]
            self._save(data, username, state)
            return {"state": public_state(state), "result": result}

    def focused_tab(self, username: str, state: dict) -> dict | None:
        """CrossPet 用：最近聚焦标签（仍存在）优先，否则主窗口 active。"""
        with self.lock:
            tab = find_tab(state, self.focused.get(username, ""))
            return tab or find_tab(state, state["active_key"])

    def relocated(self, username: str, source_workspace_id: str,
                  target_workspace_id: str, conv_id: str, label: str = "") -> dict:
        """Server-only relocation; preserve tab identity (main and detached alike)."""
        with self.lock:
            data = self._load()
            state = self._state(data, username)
            moved = False
            for tab in state["tabs"]:
                if (tab["kind"] == "conv" and tab.get("conversation_kind", "agent") == "agent"
                        and tab["workspace_id"] == source_workspace_id
                        and tab["conversation_id"] == conv_id):
                    tab.update(workspace_id=target_workspace_id,
                               workspace_label=label, error="")
                    moved = True
            if moved:
                self._save(data, username, state)
            return public_state(state)

    def deleted(self, username: str, workspace_id: str, conv_id: str) -> dict:
        from uuid import uuid4
        return self.command(username, uuid4().hex, "deleted", {
            "workspace_id": workspace_id, "conversation_id": conv_id,
        })["state"]
