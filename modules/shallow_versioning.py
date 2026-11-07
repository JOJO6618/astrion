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

Execution filesystem (optional ``filesystem``)
----------------------------------------------
By default this manager reads/writes files through the controller's own disk,
which is only correct when the executor and the controller share a filesystem.
When the executor is remote or containerized, pass a filesystem backend (same
duck-typed surface as ``ExecutionBackend.files``):

- pre-edit content is read from the **executor** (``read_binary``); a missing
  file is a real missing file, while a read failure raises
  ``ShallowVersioningError`` instead of being mistaken for "file did not exist";
- backups are always written under the controller ``data_dir`` (never into the
  container or the protected project tree);
- rewind writes back through the same backend with ``write_binary`` (raw bytes);
  a backend without a binary writer fails loudly instead of silently writing
  text or copying to a host mirror.

``track_edit_content`` lets a caller that already read the raw bytes on the
executor side (e.g. ``LocalExecutionBackend.track_file_edit``) hand them over,
so the controller never needs a second read of a path it may not resolve.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import posixpath
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from utils.atomic_io import replace_with_retry

from modules.i18n import tr


class ShallowVersioningError(RuntimeError):
    """Raised when shallow versioning fails."""


#: Sentinel: ``content=None`` passed explicitly means "the executor says the
#: file does not exist", while omitting the argument means "read it yourself".
_NO_CONTENT = object()

_NOT_FOUND_CODES = {
    "not_found",
    "enoent",
    "no_such_file",
    "file_not_found",
    "missing",
}


def _is_missing_error(exc: BaseException) -> bool:
    """Whether an executor read error definitely means "file does not exist"."""
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        return True
    if getattr(exc, "errno", None) == errno.ENOENT:
        return True
    text = str(exc).lower()
    return (
        "no such file" in text
        or "enoent" in text
        or "不存在" in text
        or "not found" in text
    )


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

    def __init__(
        self,
        project_path: Path | str,
        data_dir: Path | str,
        conversation_id: str,
        filesystem: Any = None,
    ):
        """``filesystem``（可选）：执行侧文件后端（同 ``ExecutionBackend.files``）。

        缺省时沿用控制端本机磁盘语义（仅适用于共享文件系统的本机执行）。
        传入后，读取/写回/删除一律经该后端，并改用**纯词法**工作区根：
        ``Path.resolve()`` 会跟随控制端符号链接（例如 macOS 的
        ``/var`` → ``/private/var``），把控制端磁盘身份混进远端路径；路径
        身份应来自执行侧。``data_dir`` 是控制端备份目录，仍真实 resolve。
        """
        raw_project = Path(project_path).expanduser()
        self.project_path = (
            Path(os.path.abspath(str(raw_project)))
            if filesystem is not None
            else raw_project.resolve()
        )
        self.filesystem = filesystem
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
        """把路径统一成会话内稳定的跟踪键。

        - 注入 filesystem 时：**纯词法 + 目标端权威**。绝不调用
          ``Path.resolve()``（那会去 stat 控制端磁盘、跟随控制端符号链接，
          对容器/远端路径必定是错的）；优先用 ``filesystem.resolve_path``
          拿到目标端认的作者路径。
        - 未注入时：沿用控制端本机语义（原有行为不变）。
        """
        if self.filesystem is not None:
            from_fs = self._normalize_via_filesystem(str(file_path))
            if from_fs:
                return from_fs
            return self._normalize_lexical(str(file_path))

        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = self.project_path / path
        try:
            rel = path.resolve().relative_to(self.project_path)
            return str(rel).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def _normalize_via_filesystem(self, path: str) -> Optional[str]:
        """用目标端 ``resolve_path`` 转成工作区相对路径；不可用时返回 None。

        这是纯授权/路径换算，不 stat、不判断存在性，所以新建文件也能归一化。
        参数兼容主机与容器后端，失败一律交给词法回退（不抛错、不猜）。
        """
        resolver = getattr(self.filesystem, "resolve_path", None)
        if not callable(resolver):
            return None
        for access in ("read", "write"):
            try:
                info = resolver(path, access)
            except Exception:  # noqa: BLE001 - 路径归一化是 best-effort
                continue
            if not isinstance(info, dict) or not info.get("success"):
                continue
            candidate = info.get("relative_path")
            if not candidate:
                candidate = info.get("path")
                if info.get("within_workspace") is False:
                    candidate = None
            if candidate:
                return str(candidate).replace("\\", "/")
        return None

    def _normalize_lexical(self, path: str) -> str:
        """纯词法归一路径（不碰文件系统，不跟符号链接）。"""
        raw = str(path).replace("\\", "/").strip()
        root = str(self.project_path).replace("\\", "/").rstrip("/")
        looks_absolute = bool(raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"))
        if looks_absolute:
            normalized = posixpath.normpath(raw)
            if normalized == root:
                return "."
            if normalized.startswith(root + "/"):
                return normalized[len(root) + 1 :]
            return normalized
        normalized = posixpath.normpath(raw)
        return "" if normalized == "." else normalized

    def _resolve_file_path(self, tracking_path: str) -> Path:
        """跟踪键 → 备份名哈希/（仅本机模式）宿主写回用的路径。

        注入 filesystem 时只做**纯词法**拼接：容器内路径并不存在于控制器
        磁盘，``Path.resolve()`` 会去 stat 控制端并可能跟错链接；这里不做
        任何文件系统访问，也不 expanduser（远端 ``~`` 与控制器无关）。
        """
        if self.filesystem is not None:
            lexical = Path(tracking_path)
            if lexical.is_absolute():
                return lexical
            return Path(os.path.normpath(str(self.project_path / tracking_path)))
        if Path(tracking_path).is_absolute():
            return Path(tracking_path).expanduser().resolve()
        return (self.project_path / tracking_path).resolve()

    def _backup_file_name(self, tracking_path: str, version: int) -> str:
        file_name_hash = hashlib.sha256(tracking_path.encode("utf-8")).hexdigest()[:16]
        return f"{file_name_hash}@v{version}"

    def _backup_path(self, backup_file_name: str) -> Path:
        return (self.backup_dir / backup_file_name).resolve()

    # ----------------------------
    # source (executor) access
    # ----------------------------
    def _ensure_ok(self, result: Any, action: str, tracking_path: str) -> None:
        """把执行侧文件后端的 dict 结果转成成功/异常，绝不静默忽略失败。"""
        if isinstance(result, dict) and not result.get("success"):
            raise ShallowVersioningError(
                f"执行侧{action}失败: {tracking_path}: {result.get('error') or 'unknown'}"
            )

    def _read_source_bytes(
        self, tracking_path: str, resolved: Path
    ) -> Optional[bytes]:
        """读取文件原始内容；None 表示**执行侧确认不存在**。

        - 未注入 filesystem：控制端本机读取（共享文件系统场景）；
        - 已注入：只走 ``read_binary``，宿主路径不参与内容判断——Docker 下宿主
          镜像可能是旧副本或根本不存在，不能当权威源；
        - 读失败/权限拒绝一律抛 ``ShallowVersioningError``，不冒充“文件不存在”。
        """
        fs = self.filesystem
        if fs is None:
            try:
                return resolved.read_bytes()
            except FileNotFoundError:
                return None
            except OSError as exc:
                if _is_missing_error(exc):
                    return None
                raise ShallowVersioningError(
                    f"读取备份源失败: {resolved}: {exc}"
                ) from exc

        reader = getattr(fs, "read_binary", None)
        if not callable(reader):
            raise ShallowVersioningError(
                "执行侧文件后端缺少 read_binary，无法读取原始内容（拒绝回退宿主路径）"
            )
        try:
            data = reader(tracking_path)
        except Exception as exc:  # noqa: BLE001 - 只区分“不存在”与其它失败
            if _is_missing_error(exc):
                return None
            raise ShallowVersioningError(
                f"执行侧读取 {tracking_path} 失败: {exc}"
            ) from exc
        if data is None:
            return None
        if isinstance(data, (bytes, bytearray, memoryview)):
            return bytes(data)
        if isinstance(data, dict):
            if data.get("success"):
                content = data.get("content")
                if content is None:
                    return b""
                if isinstance(content, str):
                    return content.encode("utf-8")
                return bytes(content)
            error_code = str(data.get("error_code") or "").lower()
            if error_code in _NOT_FOUND_CODES or (
                data.get("exists") is False and not data.get("error")
            ):
                return None
            raise ShallowVersioningError(
                f"执行侧读取 {tracking_path} 失败: {data.get('error') or 'unknown'}"
            )
        raise ShallowVersioningError(
            f"执行侧 read_binary({tracking_path}) 返回了不支持的结果类型: {type(data).__name__}"
        )

    def _source_exists(self, tracking_path: str, resolved: Path) -> bool:
        """执行侧文件是否存在（读失败时抛错，不把不可达当作不存在）。"""
        fs = self.filesystem
        if fs is None:
            return resolved.exists()
        inspect = getattr(fs, "inspect_path", None)
        if callable(inspect):
            try:
                info = inspect(tracking_path, "read")
            except Exception as exc:  # noqa: BLE001
                if _is_missing_error(exc):
                    return False
                raise ShallowVersioningError(
                    f"执行侧检查 {tracking_path} 失败: {exc}"
                ) from exc
            if isinstance(info, dict):
                if info.get("success"):
                    return bool(info.get("exists"))
                error_code = str(info.get("error_code") or "").lower()
                if error_code in _NOT_FOUND_CODES or (
                    info.get("exists") is False and not info.get("error")
                ):
                    return False
                raise ShallowVersioningError(
                    f"执行侧检查 {tracking_path} 失败: {info.get('error') or 'unknown'}"
                )
        return self._read_source_bytes(tracking_path, resolved) is not None

    def _write_source(
        self, tracking_path: str, resolved: Path, data: bytes
    ) -> None:
        """把快照以**原始字节**写回执行侧；不向宿主镜像复制。

        注入 filesystem 时严格要求 ``write_binary``：文本通道会改编码/丢字节，
        宁可报错也不隐藏失败（这是公共层该补的能力，不使用降级分支）。
        """
        fs = self.filesystem
        if fs is None:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_bytes(data)
            return
        writer = getattr(fs, "write_binary", None)
        if not callable(writer):
            raise ShallowVersioningError(
                "执行侧文件后端缺少 write_binary，拒绝用文本通道写回（可能改编码/丢字节）: "
                f"无法恢复 {tracking_path}"
            )
        self._ensure_ok(writer(tracking_path, data), "写入", tracking_path)

    def _delete_source(self, tracking_path: str, resolved: Path) -> None:
        """删除执行侧文件（回滚“新建”）。“"""
        fs = self.filesystem
        if fs is None:
            resolved.unlink()
            return
        deleter = getattr(fs, "delete_file", None)
        if not callable(deleter):
            raise ShallowVersioningError(
                "执行侧文件后端缺少 delete_file，无法回滚新建文件"
            )
        self._ensure_ok(deleter(tracking_path), "删除", tracking_path)

    # ----------------------------
    # backup operations
    # ----------------------------
    def _create_backup(
        self,
        tracking_path: str,
        resolved: Path,
        version: int,
        content: Any = _NO_CONTENT,
    ) -> FileBackup:
        """把编辑前内容备份到控制端 data_dir（内容来源：执行侧）。

        ``content`` 显式传入时直接使用（已经由执行侧读出，无需二次读取）；
        ``content is None`` 显式表示执行侧已确认文件不存在。
        """
        backup_file_name = self._backup_file_name(str(resolved), version)
        backup_path = self._backup_path(backup_file_name)

        if content is _NO_CONTENT:
            data: Optional[bytes] = self._read_source_bytes(tracking_path, resolved)
        else:
            data = content

        if data is None:
            # 执行侧确认文件不存在：不建目录、不写空备份，只记一个 None 版本
            return FileBackup(
                backup_file_name=None,
                version=version,
                backup_time=datetime.now().isoformat(),
            )

        # 备份只写控制端 data_dir；目录延迟到真有内容时才建（读取失败不留痕）
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(bytes(data))
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

    def _file_changed_since_backup(
        self, tracking_path: str, resolved: Path, backup: FileBackup
    ) -> bool:
        """当前内容是否已与备份不同（当前内容同样来自执行侧）。"""
        if backup.backup_file_name is None:
            return self._source_exists(tracking_path, resolved)
        backup_path = self._backup_path(backup.backup_file_name)
        if not backup_path.exists():
            return True
        try:
            current = self._read_source_bytes(tracking_path, resolved)
        except ShallowVersioningError:
            # 读不到当前内容时保守地当作“已变更”，后续重新备份会再次报错，
            # 而不是把未读到的状态当成“未变”。
            return True
        if current is None:
            return True
        try:
            return bytes(current) != backup_path.read_bytes()
        except OSError:
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

    def track_edit(
        self,
        file_path: Path | str,
        message_id: str,
        *,
        content: Any = _NO_CONTENT,
    ) -> None:
        """Track a file before it is edited; backup its current content if not already tracked.

        ``content``（可选）：执行侧已读出的原始字节。显式传 ``None`` 表示执行侧
        已确认文件不存在；不传则由 ``filesystem``（或本机磁盘）自行读取。
        """
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
            backup = self._create_backup(tracking_path, resolved, 1, content=content)
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

    def track_edit_content(
        self,
        file_path: Path | str,
        message_id: str,
        content: Optional[bytes],
    ) -> None:
        """用**执行侧已读出的原始字节**登记备份，不再从控制端磁盘重读。

        ``content=None`` 表示执行侧已确认文件不存在（记一个 backup_name=None 版本）。
        适用于 Docker/远端：`LocalExecutionBackend.track_file_edit` 先用
        ``files.read_binary`` 拿到权威内容，再交给本方法。
        """
        if content is not None and not isinstance(
            content, (bytes, bytearray, memoryview)
        ):
            raise ShallowVersioningError(
                f"track_edit_content 需要 bytes 或 None，收到: {type(content).__name__}"
            )
        payload: Any = None if content is None else bytes(content)
        self.track_edit(file_path, message_id, content=payload)

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

                if latest_backup and not self._file_changed_since_backup(
                    tracking_path, resolved, latest_backup
                ):
                    tracked_file_backups[tracking_path] = latest_backup
                    continue

                tracked_file_backups[tracking_path] = self._create_backup(
                    tracking_path, resolved, next_version
                )

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
                if self._source_exists(tracking_path, resolved):
                    self._delete_source(tracking_path, resolved)
                    files_changed.append(tracking_path)
                continue

            backup_path = self._backup_path(target_backup.backup_file_name)
            if not backup_path.exists():
                continue
            if self._file_changed_since_backup(tracking_path, resolved, target_backup):
                self._write_source(tracking_path, resolved, backup_path.read_bytes())
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
