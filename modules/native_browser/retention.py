"""Decide which desktop browser groups can release their native pages."""
from __future__ import annotations

from dataclasses import dataclass

MAX_RESIDENT_GROUPS = 3


@dataclass(frozen=True)
class BrowserGroup:
    visit: int | None
    closed: bool = False
    running: bool = False
    exists: bool | None = True


def decide_retention(groups: dict[str, BrowserGroup]) -> dict[str, list[str]]:
    deleted = sorted(key for key, group in groups.items() if group.exists is False)
    resident = {key: group for key, group in groups.items()
                if group.visit is not None and group.exists is not False}
    releasable = {key: group for key, group in resident.items()
                  if not group.running and group.exists is True}
    closed = sorted(key for key, group in releasable.items() if group.closed)
    capacity = max(0, len(resident) - len(closed) - MAX_RESIDENT_GROUPS)
    oldest = sorted((key for key, group in releasable.items() if not group.closed),
                    key=lambda key: (releasable[key].visit, key))
    return {"deleted": deleted, "release": closed + oldest[:capacity]}
