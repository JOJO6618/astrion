"""Chat-only global entries and topical memories, isolated from Agent memory."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re

from utils.atomic_io import atomic_write_json
from utils.conversation_manager.locking import directory_lock


class ChatMemory:
    def __init__(self, root: Path):
        self.root = Path(root) / "memory"
        self.path = self.root / "memories.json"
        self._lock = directory_lock(self.root)

    def _load(self):
        if not self.path.exists():
            return {"version": 1, "global": [], "topics": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("global"), list) or not isinstance(data.get("topics"), dict):
            raise ValueError("Invalid Chat memory document")
        return data

    def snapshot(self):
        with self._lock:
            return deepcopy(self._load())

    def _read_entries(self, memory_type="main"):
        if memory_type != "main":
            raise ValueError("Chat has no Agent task memory")
        return self.snapshot()["global"]

    def update(self, operation, content=None, index=None):
        if operation not in {"append", "replace", "delete"}:
            raise ValueError("Invalid memory operation")
        if operation != "delete" and (not isinstance(content, str) or not content.strip()):
            raise ValueError("Memory content is required")
        with self._lock:
            data = self._load()
            entries = data["global"]
            if operation != "append" and (type(index) is not int or not 1 <= index <= len(entries)):
                raise ValueError("Memory index is out of range")
            if operation == "append":
                entries.append(content.strip())
            elif operation == "replace":
                entries[index - 1] = content.strip()
            else:
                entries.pop(index - 1)
            atomic_write_json(self.path, data)
            return {"success": True, "count": len(entries), "operation": operation}

    def write_topic(self, name, description, content):
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", name):
            raise ValueError("Memory names use lowercase letters, digits and underscores")
        if not isinstance(description, str) or not description.strip() or len(description) > 100:
            raise ValueError("A description of at most 100 characters is required")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Topic content is required")
        with self._lock:
            data = self._load()
            data["topics"][name] = {"name": name, "description": description,
                                    "content": content, "updated_at": datetime.now().isoformat()}
            atomic_write_json(self.path, data)
        return {"success": True, "name": name}

    def read_topic(self, name):
        topic = self.snapshot()["topics"].get(name)
        if topic is None:
            raise ValueError("Chat memory not found")
        return topic

    def search(self, keywords, max_results=5):
        if not isinstance(keywords, list) or not 1 <= len(keywords) <= 5 or any(
            not isinstance(k, str) or not k.strip() for k in keywords
        ):
            raise ValueError("Provide one to five nonempty keywords")
        ranked = []
        for topic in self.snapshot()["topics"].values():
            haystack = "\n".join(topic[k] for k in ("name", "description", "content")).casefold()
            score = sum(k.casefold() in haystack for k in keywords)
            if score:
                ranked.append((score, {"name": topic["name"], "description": topic["description"],
                                       "snippet": topic["content"][:400]}))
        ranked.sort(key=lambda x: (-x[0], x[1]["name"]))
        return [item for _, item in ranked[:max(1, min(max_results or 5, 10))]]
