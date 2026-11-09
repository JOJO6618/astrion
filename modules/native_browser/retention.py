"""Desktop browser residency policy; shared by Electron and WebView2."""
from __future__ import annotations

from threading import RLock
from typing import Any

IDLE_MS = 30 * 60 * 1000
AI_PROTECTION_MS = 60 * 60 * 1000
BACKGROUND_OWNERS = 3


class BrowserRetention:
    def __init__(self) -> None:
        self._lock = RLock()
        self._users: dict[str, dict[str, Any]] = {}

    def decide(self, username: str, owners: dict[str, dict], tabs: list[dict],
               now: int) -> dict:
        """Unknown activity is protected; only a confirmed missing file is deleted."""
        with self._lock:
            state = self._users.setdefault(username, {"front": set(), "order": {}, "clock": 0, "viewed": {}})
            deleted = {key for key, value in owners.items() if value.get("exists") is False}
            known = (set(owners) - deleted) & {tab["owner"] for tab in tabs}
            front = {key for key in known if owners[key].get("front") or owners[key].get("unknown")}
            # Refresh order only on a transition, never on polling or website activity.
            for key in sorted(known, key=lambda item: (owners[item].get("viewed", 0), item)):
                viewed = owners[key].get("viewed", 0)
                if (key not in state["order"] or key in (state["front"] ^ front)
                        or viewed > state["viewed"].get(key, 0)):
                    state["clock"] += 1
                    state["order"][key] = state["clock"]
                state["viewed"][key] = viewed
            state["viewed"] = {key: value for key, value in state["viewed"].items() if key in known}
            state["front"] = front
            state["order"] = {key: value for key, value in state["order"].items() if key in known}
            background = sorted(known - front, key=lambda key: state["order"][key], reverse=True)
            retained = front | set(background[:BACKGROUND_OWNERS])
            release = []
            for tab in tabs:
                owner = tab["owner"]
                if owner in deleted or owner not in retained:
                    release.append(tab["id"])
                elif not tab.get("selected") and not tab.get("busy"):
                    last_ai = tab.get("lastAi", 0)
                    protected = last_ai > 0 and now - last_ai < AI_PROTECTION_MS
                    if not protected and now - tab.get("lastActive", now) >= IDLE_MS:
                        release.append(tab["id"])
            return {"release": release, "deleted": sorted(deleted), "retained": sorted(retained)}


browser_retention = BrowserRetention()
