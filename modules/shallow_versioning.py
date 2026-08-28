"""Conversation-scoped shallow file versioning for workspace edits.

Mirrors the file-history approach of mainstream AI coding tools:
- Only files touched by write_file / edit_file are tracked.
- Before each edit the current content is backed up.
- A snapshot is taken per user message, referencing the latest backup version
  of every tracked file at that point in time.
- Rewind restores the tracked files to the state recorded for a target message.

Persistence model (state.jsonl)
--------------------------------
INVARIANT: the on-disk state file holds **exactly one row per message_id** —
the most complete snapshot known for that message. While a message is being
processed, its single row grows as new files get tracked (track_edit rewrites
it); when the message finishes, make_snapshot replaces it with the final full
snapshot. Every mutation rewrites the whole file atomically (temp + replace),
so the invariant holds at rest, not only in memory.

Legacy append-only logs may contain multiple rows per message_id (intermediate
track_edit journals plus the final snapshot). _load_state collapses those
duplicates keeping the last row, which heals old state files transparently;
the next mutation rewrites the file clean permanently.

Concurrency: mutations (track_edit / make_snapshot) run inside a per-state-file
process lock as reload -> mutate -> save critical sections, so short-lived
manager instances (one per edit / per task end) can never lose each other's
rows. Readers only see whole files because writes go through os.replace.

Diff bases (version fallback)
-----------------------------
The diff shown for a message compares its final snapshot against the previous
message's snapshot. A file first tracked in the target message is absent from
every earlier snapshot: make_snapshot replaces the message's intermediate
track_edit row (which held the pre-edit backup) with the final row, so the
pre-edit state can no longer be found via snapshot pairing.

It is recovered through two invariants of the backup store:

- backup file names are deterministic: sha256 of the resolved absolute path,
  suffixed with @v<version>;
- backup versions are sequential per file: track_edit creates v1 (pre-edit),
  each make_snapshot bumps at most one version, and backup files are never
  garbage-collected.

Hence for a first-appearance backup at version N >= 2, version N-1 is exactly
the pre-message state (for a file created by the message no v1 exists on disk,
so the base stays "missing" and the file correctly shows as added). A
first-appearance version-1 backup means the message left the file unchanged
since track_edit. This also heals state files written before the fix and
survives MAX_SNAPSHOTS eviction of older rows.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from utils.atomic_io import replace_with_retry

from modules.i18n import tr


class ShallowVersioningError(RuntimeError):
    """Raised when shallow versioning fails."""


@dataclass
class FileBackup:
    backup_file_name: Optional[str]  # null means the file did not exist at that version
    version: int
    backup_time: str


@dataclass
class ShallowSnapshot:
    message_id: str
    tracked_file_backups: Dict[str, FileBackup]
    timestamp: str


# Process-level locks keyed by state file path, so that separate manager
# instances targeting the same conversation serialize their mutations.
_STATE_LOCKS: Dict[str, threading.Lock] = {}
_STATE_LOCKS_GUARD = threading.Lock()


def _state_lock_for(state_file: Path) -> threading.Lock:
    key = str(state_file)
    with _STATE_LOCKS_GUARD:
        lock = _STATE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _STATE_LOCKS[key] = lock
        return lock


class ShallowVersioningManager:
    """Lightweight per-conversation backup for files edited by AI tools."""

    MAX_SNAPSHOTS = 100

    def __init__(self, project_path: Path | str, data_dir: Path | str, conversation_id: str):
        self.project_path = Path(project_path).expanduser().resolve()
        self.conversation_id = str(conversation_id or "").strip()
        if not self.conversation_id:
            raise ShallowVersioningError(tr("shallow_ver.missing_conversation_id"))
        self.save_root = (Path(data_dir).expanduser().resolve() / "save" / self.conversation_id).resolve()
        self.backup_dir = self.save_root / "shallow_backups"
        self.state_file = self.backup_dir / "state.jsonl"
        self._state_lock = _state_lock_for(self.state_file)
        self._tracked_files: Set[str] = set()
        self._snapshots: List[ShallowSnapshot] = []
        self._snapshot_sequence = 0
        self._load_state()

    # ----------------------------
    # persistence
    # ----------------------------
    def _load_state(self) -> None:
        """(Re)load snapshots from disk, collapsing duplicate rows per message_id.

        Idempotent: resets in-memory state first, so it can be called inside
        mutation critical sections to pick up rows written by other instances.
        Legacy logs with multiple rows for the same message_id keep only the
        last (most complete) row, ordered by last occurrence.
        """
        self._tracked_files = set()
        self._snapshots = []
        self._snapshot_sequence = 0
        if not self.state_file.exists():
            return
        try:
            raw_lines = self.state_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return
        parsed: List[ShallowSnapshot] = []
        for raw in raw_lines:
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            snapshot = self._deserialize_snapshot(row)
            if snapshot:
                parsed.append(snapshot)
        # Collapse duplicates: keep the last row per message_id.
        last_index: Dict[str, int] = {}
        for idx, snapshot in enumerate(parsed):
            last_index[str(snapshot.message_id or "")] = idx
        self._snapshots = [
            snapshot for idx, snapshot in enumerate(parsed) if last_index[str(snapshot.message_id or "")] == idx
        ]
        self._snapshot_sequence = len(self._snapshots)
        for snapshot in self._snapshots:
            self._tracked_files.update(snapshot.tracked_file_backups.keys())

    def _save_state(self) -> None:
        """Persist the full snapshot list atomically (temp file + os.replace).

        Must be called with self._state_lock held. Rewriting the whole file is
        what enforces the one-row-per-message invariant on disk.
        """
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_file.parent / (self.state_file.name + ".tmp")
        payload = "".join(
            json.dumps(self._serialize_snapshot(snapshot), ensure_ascii=False) + "\n"
            for snapshot in self._snapshots
        )
        tmp_path.write_text(payload, encoding="utf-8")
        # Windows 瞬时持锁（并发读取/杀软扫描）重试，POSIX 行为不变
        replace_with_retry(tmp_path, self.state_file)

    def _upsert_snapshot(self, snapshot: ShallowSnapshot) -> None:
        """Insert or replace the snapshot for its message_id in memory.

        Removes every existing entry with the same message_id and places the
        new snapshot at the position of the first removed entry (appends when
        no entry existed), preserving chronological order.
        """
        target_id = str(snapshot.message_id or "")
        first_match = -1
        for idx, existing in enumerate(self._snapshots):
            if str(existing.message_id or "") == target_id:
                first_match = idx
                break
        self._snapshots = [
            existing for existing in self._snapshots if str(existing.message_id or "") != target_id
        ]
        if first_match < 0:
            self._snapshots.append(snapshot)
            self._snapshot_sequence += 1
        else:
            insert_at = min(first_match, len(self._snapshots))
            self._snapshots.insert(insert_at, snapshot)

    def _serialize_snapshot(self, snapshot: ShallowSnapshot) -> Dict[str, Any]:
        return {
            "message_id": snapshot.message_id,
            "timestamp": snapshot.timestamp,
            "tracked_file_backups": {
                path: {
                    "backup_file_name": backup.backup_file_name,
                    "version": backup.version,
                    "backup_time": backup.backup_time,
                }
                for path, backup in snapshot.tracked_file_backups.items()
            },
        }

    def _deserialize_snapshot(self, row: Dict[str, Any]) -> Optional[ShallowSnapshot]:
        if not isinstance(row.get("message_id"), str):
            return None
        backups: Dict[str, FileBackup] = {}
        for path, backup_raw in (row.get("tracked_file_backups") or {}).items():
            if not isinstance(backup_raw, dict):
                continue
            backups[str(path)] = FileBackup(
                backup_file_name=backup_raw.get("backup_file_name"),
                version=int(backup_raw.get("version") or 1),
                backup_time=str(backup_raw.get("backup_time") or datetime.now().isoformat()),
            )
        return ShallowSnapshot(
            message_id=row["message_id"],
            tracked_file_backups=backups,
            timestamp=str(row.get("timestamp") or datetime.now().isoformat()),
        )

    # ----------------------------
    # path helpers
    # ----------------------------
    def _normalize_path(self, file_path: Path | str) -> str:
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = self.project_path / path
        try:
            rel = path.resolve().relative_to(self.project_path)
            return str(rel).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def _resolve_file_path(self, tracking_path: str) -> Path:
        if Path(tracking_path).is_absolute():
            return Path(tracking_path).expanduser().resolve()
        return (self.project_path / tracking_path).resolve()

    def _backup_file_name(self, tracking_path: str, version: int) -> str:
        file_name_hash = hashlib.sha256(tracking_path.encode("utf-8")).hexdigest()[:16]
        return f"{file_name_hash}@v{version}"

    def _backup_path(self, backup_file_name: str) -> Path:
        return (self.backup_dir / backup_file_name).resolve()

    # ----------------------------
    # backup operations
    # ----------------------------
    def _create_backup(self, file_path: Path, version: int) -> FileBackup:
        backup_file_name = self._backup_file_name(str(file_path), version)
        backup_path = self._backup_path(backup_file_name)
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        if not file_path.exists():
            return FileBackup(backup_file_name=None, version=version, backup_time=datetime.now().isoformat())

        shutil.copy2(file_path, backup_path)
        return FileBackup(
            backup_file_name=backup_file_name,
            version=version,
            backup_time=datetime.now().isoformat(),
        )

    def _get_latest_backup_version(self, tracking_path: str) -> Optional[FileBackup]:
        latest: Optional[FileBackup] = None
        for snapshot in self._snapshots:
            backup = snapshot.tracked_file_backups.get(tracking_path)
            if backup and (latest is None or backup.version > latest.version):
                latest = backup
        return latest

    def _file_changed_since_backup(self, file_path: Path, backup: FileBackup) -> bool:
        if backup.backup_file_name is None:
            return file_path.exists()
        backup_path = self._backup_path(backup.backup_file_name)
        if not file_path.exists():
            return True
        if not backup_path.exists():
            return True
        try:
            if file_path.stat().st_size != backup_path.stat().st_size:
                return True
            current = file_path.read_text(encoding="utf-8", errors="ignore")
            saved = backup_path.read_text(encoding="utf-8", errors="ignore")
            return current != saved
        except Exception:
            return True

    # ----------------------------
    # public API
    # ----------------------------
    def _find_latest_snapshot_by_message_id(self, message_id: str) -> Optional[ShallowSnapshot]:
        """Return the most recent snapshot whose message_id matches."""
        target = str(message_id or "")
        for snapshot in reversed(self._snapshots):
            if str(snapshot.message_id or "") == target:
                return snapshot
        return None

    def track_edit(self, file_path: Path | str, message_id: str) -> None:
        """Track a file before it is edited; backup its current content if not already tracked."""
        tracking_path = self._normalize_path(file_path)
        from utils.perf_log import perf_log
        perf_log("[ShallowVersioning] track_edit called", extra={
            "tracking_path": tracking_path,
            "message_id": message_id,
        })
        # Critical section: reload so rows written by other (short-lived)
        # instances are visible, then mutate and save atomically.
        with self._state_lock:
            self._load_state()
            if tracking_path in self._tracked_files:
                return

            resolved = self._resolve_file_path(tracking_path)
            backup = self._create_backup(resolved, 1)
            self._tracked_files.add(tracking_path)

            # 归属到当前 message_id 对应的 snapshot；不存在则新建。
            # 避免把本次输入的修改追加到上一个输入的 snapshot。
            now = datetime.now().isoformat()
            target_snapshot = self._find_latest_snapshot_by_message_id(message_id)
            if target_snapshot is not None:
                target_snapshot.tracked_file_backups[tracking_path] = backup
            else:
                target_snapshot = ShallowSnapshot(
                    message_id=message_id,
                    tracked_file_backups={tracking_path: backup},
                    timestamp=now,
                )
            self._upsert_snapshot(target_snapshot)
            self._save_state()

    def make_snapshot(self, message_id: str) -> Dict[str, Any]:
        """Create a snapshot for the given message, backing up changed tracked files."""
        from utils.perf_log import perf_log
        perf_log("[ShallowVersioning] make_snapshot called", extra={
            "message_id": message_id,
        })
        # Critical section: reload so track_edit rows from other instances are
        # visible before deciding what changed and which version comes next.
        with self._state_lock:
            self._load_state()
            tracked_file_backups: Dict[str, FileBackup] = {}
            now = datetime.now().isoformat()

            for tracking_path in self._tracked_files:
                resolved = self._resolve_file_path(tracking_path)
                latest_backup = self._get_latest_backup_version(tracking_path)
                next_version = (latest_backup.version + 1) if latest_backup else 1

                if latest_backup and not self._file_changed_since_backup(resolved, latest_backup):
                    tracked_file_backups[tracking_path] = latest_backup
                    continue

                tracked_file_backups[tracking_path] = self._create_backup(resolved, next_version)

            snapshot = ShallowSnapshot(
                message_id=message_id,
                tracked_file_backups=tracked_file_backups,
                timestamp=now,
            )
            # 一条消息只保留一个快照：替换 track_edit 阶段留下的进行中的行。
            self._upsert_snapshot(snapshot)

            # Evict old snapshots while keeping the most recent MAX_SNAPSHOTS.
            if len(self._snapshots) > self.MAX_SNAPSHOTS:
                self._snapshots = self._snapshots[-self.MAX_SNAPSHOTS :]

            self._save_state()

        perf_log("[ShallowVersioning] make_snapshot done", extra={
            "message_id": message_id,
            "tracked_files_count": len(tracked_file_backups),
            "backups": {k: v.backup_file_name for k, v in tracked_file_backups.items()},
        })
        return {
            "message_id": message_id,
            "tracked_files_count": len(tracked_file_backups),
            "timestamp": now,
        }

    def can_restore(self, message_id: str) -> bool:
        return any(snapshot.message_id == message_id for snapshot in self._snapshots)

    def rewind(self, message_id: str) -> Dict[str, Any]:
        """Restore tracked files to the state recorded for the target message."""
        target_snapshot: Optional[ShallowSnapshot] = None
        for snapshot in reversed(self._snapshots):
            if snapshot.message_id == message_id:
                target_snapshot = snapshot
                break
        if not target_snapshot:
            raise ShallowVersioningError(tr("shallow_ver.snapshot_not_found", message_id=message_id))

        files_changed: List[str] = []
        for tracking_path in self._tracked_files:
            target_backup = target_snapshot.tracked_file_backups.get(tracking_path)
            if not target_backup:
                # File was not tracked at the target snapshot; leave it unchanged.
                continue

            resolved = self._resolve_file_path(tracking_path)
            if target_backup.backup_file_name is None:
                if resolved.exists():
                    resolved.unlink()
                    files_changed.append(tracking_path)
                continue

            backup_path = self._backup_path(target_backup.backup_file_name)
            if not backup_path.exists():
                continue
            if self._file_changed_since_backup(resolved, target_backup):
                resolved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, resolved)
                files_changed.append(tracking_path)

        return {
            "success": True,
            "message_id": message_id,
            "files_changed": files_changed,
        }

    def _read_backup_lines(self, backup: Optional[FileBackup]) -> List[str]:
        """Read lines from a backup entry; empty list for missing/deleted files."""
        if not backup or backup.backup_file_name is None:
            return []
        backup_path = self._backup_path(backup.backup_file_name)
        if not backup_path.exists():
            return []
        try:
            return backup_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return []

    def _count_line_changes(self, previous_lines: List[str], target_lines: List[str]) -> tuple[int, int]:
        """Count real insertions/deletions between two line lists using SequenceMatcher."""
        if previous_lines == target_lines:
            return 0, 0
        try:
            import difflib
            sm = difflib.SequenceMatcher(None, previous_lines, target_lines)
        except Exception:
            # Fallback to naive delta if difflib fails.
            if len(previous_lines) > len(target_lines):
                return 0, len(previous_lines) - len(target_lines)
            return len(target_lines) - len(previous_lines), 0

        insertions = 0
        deletions = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "insert":
                insertions += j2 - j1
            elif tag == "delete":
                deletions += i2 - i1
            elif tag == "replace":
                insertions += j2 - j1
                deletions += i2 - i1
        return insertions, deletions

    def _find_snapshot_pair(self, message_id: str):
        """Return (target_snapshot, previous_snapshot) for the given message_id.

        The previous snapshot is the nearest preceding snapshot belonging to a
        *different* message_id, so duplicate rows of the same message can never
        be mistaken for the diff base.
        """
        target_snapshot: Optional[ShallowSnapshot] = None
        target_index = -1
        target_id = str(message_id or "")
        for idx, snapshot in enumerate(self._snapshots):
            if str(snapshot.message_id or "") == target_id:
                target_snapshot = snapshot
                target_index = idx
        if not target_snapshot or target_index < 0:
            return None, None
        previous_snapshot: Optional[ShallowSnapshot] = None
        for idx in range(target_index - 1, -1, -1):
            if str(self._snapshots[idx].message_id or "") != target_id:
                previous_snapshot = self._snapshots[idx]
                break
        return target_snapshot, previous_snapshot

    def _resolve_diff_base_backup(
        self,
        tracking_path: str,
        target_backup: FileBackup,
        previous_snapshot: Optional[ShallowSnapshot],
    ) -> Optional[FileBackup]:
        """Resolve the backup representing the file's state BEFORE the target message.

        Normal case: the previous message snapshot references the file, so its
        backup is the diff base. A file first tracked in the target message is
        absent from every earlier snapshot (its intermediate track_edit row was
        replaced by make_snapshot); the pre-message state is then recovered via
        the version fallback documented in the module docstring: version N-1 of
        the deterministic backup name, used only when that backup file still
        exists on disk. A first-appearance version-1 backup means the file was
        left unchanged since track_edit, so it is diffed against itself.
        """
        if previous_snapshot is not None:
            previous = previous_snapshot.tracked_file_backups.get(tracking_path)
            if previous is not None:
                return previous
        if target_backup.version >= 2:
            resolved = self._resolve_file_path(tracking_path)
            candidate_name = self._backup_file_name(str(resolved), target_backup.version - 1)
            if self._backup_path(candidate_name).exists():
                return FileBackup(
                    backup_file_name=candidate_name,
                    version=target_backup.version - 1,
                    backup_time=target_backup.backup_time,
                )
        elif target_backup.version == 1 and target_backup.backup_file_name is not None:
            return target_backup
        return None

    def get_diff_stats(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Compute insertions/deletions/files_changed between the target snapshot and the previous snapshot."""
        target_snapshot, previous_snapshot = self._find_snapshot_pair(message_id)
        if not target_snapshot:
            return None

        files_changed: List[str] = []
        insertions = 0
        deletions = 0

        for tracking_path in self._tracked_files:
            target_backup = target_snapshot.tracked_file_backups.get(tracking_path)
            if not target_backup:
                continue

            previous_backup = self._resolve_diff_base_backup(tracking_path, target_backup, previous_snapshot)
            previous_lines = self._read_backup_lines(previous_backup)
            target_lines = self._read_backup_lines(target_backup)

            if previous_lines == target_lines:
                continue

            files_changed.append(tracking_path)
            ins, dels = self._count_line_changes(previous_lines, target_lines)
            insertions += ins
            deletions += dels

        return {
            "filesChanged": files_changed,
            "insertions": insertions,
            "deletions": deletions,
        }

    def has_any_changes(self, message_id: str) -> bool:
        stats = self.get_diff_stats(message_id)
        if not stats:
            return False
        return bool(stats.get("filesChanged"))

    def get_file_diff_stats(self, message_id: str) -> List[Dict[str, Any]]:
        """Return per-file insertions/deletions/status for a target snapshot vs previous snapshot."""
        target_snapshot, previous_snapshot = self._find_snapshot_pair(message_id)
        if not target_snapshot:
            return []

        result: List[Dict[str, Any]] = []
        for tracking_path in self._tracked_files:
            target_backup = target_snapshot.tracked_file_backups.get(tracking_path)
            if not target_backup:
                continue

            previous_backup = self._resolve_diff_base_backup(tracking_path, target_backup, previous_snapshot)
            previous_lines = self._read_backup_lines(previous_backup)
            target_lines = self._read_backup_lines(target_backup)

            if previous_lines == target_lines:
                continue

            insertions, deletions = self._count_line_changes(previous_lines, target_lines)

            status = "deleted" if target_backup.backup_file_name is None else ("added" if previous_backup is None or previous_backup.backup_file_name is None else "modified")
            result.append({
                "path": tracking_path,
                "status": status,
                "insertions": insertions,
                "deletions": deletions,
            })
        return result

    def get_file_patch_lines(
        self,
        tracking_path: str,
        message_id: str,
        max_lines: int = 600,
    ) -> Dict[str, Any]:
        """Return contextual patch lines between target snapshot and previous snapshot."""
        target_snapshot, previous_snapshot = self._find_snapshot_pair(message_id)
        if not target_snapshot:
            return {"lines": [], "truncated": False}

        target_backup = target_snapshot.tracked_file_backups.get(tracking_path)
        if not target_backup:
            return {"lines": [], "truncated": False}

        previous_backup = self._resolve_diff_base_backup(tracking_path, target_backup, previous_snapshot)
        previous_lines = self._read_backup_lines(previous_backup)
        target_lines = self._read_backup_lines(target_backup)

        if previous_lines == target_lines:
            return {"lines": [], "truncated": False}

        try:
            import difflib
            sm = difflib.SequenceMatcher(None, previous_lines, target_lines)
        except Exception:
            return {"lines": [], "truncated": False}

        lines: List[Dict[str, Any]] = []
        truncated = False
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            if tag == "replace" or tag == "delete":
                for line in previous_lines[i1:i2]:
                    if len(lines) >= max_lines:
                        truncated = True
                        break
                    lines.append({"type": "remove", "content": line})
            if tag == "replace" or tag == "insert":
                for line in target_lines[j1:j2]:
                    if len(lines) >= max_lines:
                        truncated = True
                        break
                    lines.append({"type": "add", "content": line})
            if truncated:
                break
        return {"lines": lines, "truncated": truncated}

    def get_snapshot_by_seq(self, seq: int) -> Optional[ShallowSnapshot]:
        """Return the user-message snapshot by 1-based index (skips the initial track_edit snapshot)."""
        user_snapshots = [s for s in self._snapshots if s.message_id != self.conversation_id]
        idx = int(seq) - 1
        if idx < 0 or idx >= len(user_snapshots):
            return None
        return user_snapshots[idx]

    def list_snapshots(self) -> List[Dict[str, Any]]:
        return [self._serialize_snapshot(s) for s in self._snapshots]
