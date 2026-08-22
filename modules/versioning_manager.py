"""Conversation-scoped hidden git versioning for host workspace."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import deque

from utils.perf_log import perf_log


class VersioningError(RuntimeError):
    """Raised when hidden git versioning fails."""


@dataclass
class VersioningPaths:
    save_root: Path
    git_dir: Path
    meta_path: Path
    inputs_path: Path
    snapshots_dir: Path


class ConversationVersioningManager:
    """Manage hidden git snapshots bound to a conversation."""
    TRACKING_MODE_WORKSPACE_AND_CONVERSATION = "workspace_and_conversation"
    TRACKING_MODE_CONVERSATION_ONLY = "conversation_only"
    SYSTEM_AUTO_DIRS = (".astrion/compact_result", ".astrion/skills", ".astrion/user_upload")
    # 空 Git tree 对象的固定哈希，用于 tree-to-tree diff 的基准点
    EMPTY_TREE_HASH = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


    def __init__(self, project_path: Path | str, data_dir: Path | str, conversation_id: str):
        self.project_path = Path(project_path).expanduser().resolve()
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.conversation_id = str(conversation_id or "").strip()
        if not self.conversation_id:
            raise VersioningError("缺少 conversation_id")
        self.paths = VersioningPaths(
            save_root=(self.data_dir / "save" / self.conversation_id).resolve(),
            git_dir=(self.data_dir / "save" / self.conversation_id / "git").resolve(),
            meta_path=(self.data_dir / "save" / self.conversation_id / "meta.json").resolve(),
            inputs_path=(self.data_dir / "save" / self.conversation_id / "inputs.jsonl").resolve(),
            snapshots_dir=(self.data_dir / "save" / self.conversation_id / "snapshots").resolve(),
        )

    @classmethod
    def normalize_tracking_mode(cls, mode: Optional[str]) -> str:
        raw = str(mode or "").strip().lower()
        if raw == cls.TRACKING_MODE_CONVERSATION_ONLY:
            return cls.TRACKING_MODE_CONVERSATION_ONLY
        return cls.TRACKING_MODE_WORKSPACE_AND_CONVERSATION

    # ----------------------------
    # low-level helpers
    # ----------------------------
    def _run_git(
        self,
        args: List[str],
        *,
        check: bool = True,
        timeout_seconds: int = 180,
    ) -> Tuple[bool, str, str]:
        git_bin = shutil.which("git")
        if not git_bin:
            raise VersioningError("未检测到 git 可执行文件")
        cmd = [
            git_bin,
            "-c",
            "core.quotepath=false",
            f"--git-dir={self.paths.git_dir}",
            f"--work-tree={self.project_path}",
            *args,
        ]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                # git 输出固定为 UTF-8；Windows text=True 默认按 locale（cp936）
                # 解码会让中文路径/提交信息乱码（同 server/status/git.py 的处理）
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise VersioningError(f"git 执行超时: {' '.join(args)}") from exc
        ok = completed.returncode == 0
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if check and not ok:
            raise VersioningError(stderr or stdout or f"git 命令失败: {' '.join(args)}")
        return ok, stdout, stderr

    def _ensure_exclude_paths(self) -> None:
        """Avoid recursive tracking if save root is inside work tree."""
        info_exclude = self.paths.git_dir / "info" / "exclude"
        info_exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if info_exclude.exists():
            try:
                existing = info_exclude.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                existing = ""
        existing_lines = set(existing.splitlines())
        lines_to_add: List[str] = []

        try:
            rel_save = self.paths.save_root.relative_to(self.project_path)
            marker = str(rel_save).replace("\\", "/").rstrip("/")
            if marker:
                lines_to_add.append(f"{marker}/")
        except ValueError:
            pass

        for dirname in self.SYSTEM_AUTO_DIRS:
            clean = str(dirname or "").strip().strip("/").replace("\\", "/")
            if not clean:
                continue
            # 同时忽略根目录与任意层级同名目录
            lines_to_add.append(f"/{clean}/")
            lines_to_add.append(f"{clean}/")

        pending = [line for line in lines_to_add if line not in existing_lines]
        if not pending:
            return
        if existing and not existing.endswith("\n"):
            pending.insert(0, "")
        with info_exclude.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(pending) + "\n")

    def _drop_ignored_from_index(self) -> None:
        """
        Ensure ignored system directories are not kept as tracked files in hidden git index.
        """
        for dirname in self.SYSTEM_AUTO_DIRS:
            clean = str(dirname or "").strip().strip("/").replace("\\", "/")
            if not clean:
                continue
            self._run_git(["rm", "-r", "--cached", "--ignore-unmatch", "--", clean], check=False)

    def _stage_all(self) -> None:
        t0 = time.perf_counter()
        self._ensure_exclude_paths()
        self._drop_ignored_from_index()
        perf_log("versioning _stage_all before add", elapsed_ms=(time.perf_counter() - t0) * 1000)
        # Stage everything under work tree; exclusions are handled via info/exclude.
        t1 = time.perf_counter()
        self._run_git(["add", "-A", "--", "."])
        perf_log("versioning _stage_all add", elapsed_ms=(time.perf_counter() - t1) * 1000)
        perf_log("versioning _stage_all done", elapsed_ms=(time.perf_counter() - t0) * 1000)

    def _get_head_tree(self) -> Optional[str]:
        """获取当前 index 对应的 tree hash；index 为空时返回空树对象哈希。"""
        ok, out, _ = self._run_git(["write-tree"], check=False)
        if ok and out:
            return out
        # 空 tree 的 hash 是固定的 Git 常量
        return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

    def _is_clean(self) -> bool:
        ok, out, _ = self._run_git(["status", "--porcelain"], check=False)
        if not ok:
            return False
        return not bool(out.strip())

    # ----------------------------
    # metadata / records
    # ----------------------------
    def load_meta(self) -> Dict[str, Any]:
        if not self.paths.meta_path.exists():
            return {
                "enabled": False,
                "mode": "overwrite",
                "tracking_mode": self.TRACKING_MODE_WORKSPACE_AND_CONVERSATION,
                "last_tree_hash": None,
                "last_input_seq": 0,
                "updated_at": None,
            }
        try:
            payload = json.loads(self.paths.meta_path.read_text(encoding="utf-8")) or {}
            payload["tracking_mode"] = self.normalize_tracking_mode(payload.get("tracking_mode"))
            return payload
        except Exception:
            return {
                "enabled": False,
                "mode": "overwrite",
                "tracking_mode": self.TRACKING_MODE_WORKSPACE_AND_CONVERSATION,
                "last_tree_hash": None,
                "last_input_seq": 0,
                "updated_at": None,
            }

    def save_meta(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(meta or {})
        payload["tracking_mode"] = self.normalize_tracking_mode(payload.get("tracking_mode"))
        payload["updated_at"] = datetime.now().isoformat()
        self.paths.meta_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def _read_inputs(self) -> List[Dict[str, Any]]:
        if not self.paths.inputs_path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        try:
            for raw in self.paths.inputs_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(self._normalize_checkpoint_row(row))
        except OSError:
            return []
        return rows

    def _append_input(self, row: Dict[str, Any]) -> None:
        self.paths.inputs_path.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.inputs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_inputs(self, rows: List[Dict[str, Any]]) -> None:
        self.paths.inputs_path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(json.dumps(row, ensure_ascii=False) for row in (rows or []))
        if content:
            content += "\n"
        self.paths.inputs_path.write_text(content, encoding="utf-8")

    def _decode_git_path(self, raw_path: Any) -> str:
        text = str(raw_path or "")
        stripped = text.strip()
        if len(stripped) >= 2 and stripped[0] == '"' and stripped[-1] == '"':
            body = stripped[1:-1]
            try:
                decoded = bytes(body, "utf-8").decode("unicode_escape")
            except Exception:
                decoded = body
            try:
                return decoded.encode("latin-1").decode("utf-8")
            except Exception:
                return decoded
        return text

    def _normalize_checkpoint_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(row or {})
        payload["tracking_mode"] = self.normalize_tracking_mode(payload.get("tracking_mode"))
        files = payload.get("files")
        if isinstance(files, list):
            normalized_files: List[Dict[str, Any]] = []
            for file_item in files:
                if not isinstance(file_item, dict):
                    continue
                item = dict(file_item)
                item["path"] = self._decode_git_path(item.get("path"))
                if item.get("old_path") is not None:
                    item["old_path"] = self._decode_git_path(item.get("old_path"))
                normalized_files.append(item)
            payload["files"] = normalized_files
        return payload

    def _write_snapshot(self, seq: int, payload: Dict[str, Any]) -> str:
        self.paths.snapshots_dir.mkdir(parents=True, exist_ok=True)
        target = self.paths.snapshots_dir / f"{int(seq)}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(target.relative_to(self.paths.save_root))

    def get_checkpoint_snapshot(self, seq: int) -> Optional[Dict[str, Any]]:
        row = self.get_checkpoint(seq)
        if not row:
            return None
        rel = row.get("snapshot_file")
        if not rel:
            return None
        path = (self.paths.save_root / str(rel)).resolve()
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8")) or None
        except Exception:
            return None

    # ----------------------------
    # repo lifecycle
    # ----------------------------
    def ensure_repo(self) -> Dict[str, Any]:
        t0 = time.perf_counter()
        self.paths.save_root.mkdir(parents=True, exist_ok=True)
        self.project_path.mkdir(parents=True, exist_ok=True)
        if not self.paths.git_dir.exists():
            self.paths.git_dir.mkdir(parents=True, exist_ok=True)
        if not (self.paths.git_dir / "HEAD").exists():
            self._run_git(["init"])
            self._run_git(["config", "user.name", "EasyAgent Versioning"], check=False)
            self._run_git(["config", "user.email", "easyagent-versioning@local"], check=False)
            perf_log("versioning ensure_repo init", elapsed_ms=(time.perf_counter() - t0) * 1000)
        t1 = time.perf_counter()
        self._ensure_exclude_paths()
        perf_log("versioning ensure_repo exclude", elapsed_ms=(time.perf_counter() - t1) * 1000)
        # 使用 write-tree 获取当前 index 的树对象哈希，不创建 commit，不污染 git 历史。
        t2 = time.perf_counter()
        head = self._get_head_tree()
        perf_log("versioning ensure_repo write_tree", elapsed_ms=(time.perf_counter() - t2) * 1000)
        perf_log("versioning ensure_repo done", elapsed_ms=(time.perf_counter() - t0) * 1000)
        return {"ok": True, "head": head}

    def set_enabled(
        self,
        enabled: bool,
        mode: Optional[str] = None,
        *,
        tracking_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        meta = self.load_meta()
        normalized_tracking_mode = self.normalize_tracking_mode(
            tracking_mode or meta.get("tracking_mode")
        )
        if enabled and normalized_tracking_mode == self.TRACKING_MODE_WORKSPACE_AND_CONVERSATION:
            self.ensure_repo()
        meta["enabled"] = bool(enabled)
        meta["mode"] = "overwrite"
        meta["tracking_mode"] = normalized_tracking_mode
        meta["last_tree_hash"] = self._get_head_tree()
        saved = self.save_meta(meta)
        return saved

    def ensure_initial_checkpoint(
        self,
        *,
        workspace_path: Optional[str] = None,
        conversation_snapshot: Optional[Dict[str, Any]] = None,
        tracking_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ensure a visible seq=0 checkpoint exists after enabling versioning.
        """
        normalized_tracking_mode = self.normalize_tracking_mode(tracking_mode)
        if normalized_tracking_mode == self.TRACKING_MODE_WORKSPACE_AND_CONVERSATION:
            self.ensure_repo()
        existing = self.get_checkpoint(0)
        if existing:
            return {"created": False, "row": existing, "reason": "already_exists"}

        current_head = self._get_head_tree()
        if normalized_tracking_mode == self.TRACKING_MODE_WORKSPACE_AND_CONVERSATION and not current_head:
            raise VersioningError("创建初始版本点失败：未获取到 commit")

        row = {
            "seq": 0,
            "message": "版本管理开启（初始状态）",
            "message_preview": "版本管理开启（初始状态）",
            "message_index": -1,
            "timestamp": datetime.now().isoformat(),
            "workspace_path": str(workspace_path or ""),
            "tree_hash": current_head,
            "parent_tree_hash": None,
            "insertions": 0,
            "deletions": 0,
            "files_changed": 0,
            "files": [],
            "changed": False,
            "run_status": "initial",
            "tracking_mode": normalized_tracking_mode,
        }
        if isinstance(conversation_snapshot, dict):
            row["snapshot_file"] = self._write_snapshot(0, conversation_snapshot)
        self._append_input(row)
        meta = self.load_meta()
        meta["tracking_mode"] = normalized_tracking_mode
        meta["last_tree_hash"] = current_head
        if int(meta.get("last_input_seq") or 0) < 0:
            meta["last_input_seq"] = 0
        self.save_meta(meta)
        return {"created": True, "row": row, "reason": "created"}

    def ensure_baseline_for_first_input(
        self,
        *,
        tracking_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ensure the baseline commit reflects current workspace before first manual input checkpoint.

        This is intentionally hidden from checkpoint list (no seq row created).
        """
        normalized_tracking_mode = self.normalize_tracking_mode(tracking_mode)
        if normalized_tracking_mode == self.TRACKING_MODE_CONVERSATION_ONLY:
            return {
                "created": False,
                "skipped": True,
                "reason": "conversation_only_mode",
                "head": self._get_head_tree(),
            }
        self.ensure_repo()
        meta = self.load_meta()
        if self.get_checkpoint(0):
            return {
                "created": False,
                "skipped": True,
                "reason": "initial_checkpoint_exists",
                "head": self._get_head_tree(),
            }
        if int(meta.get("last_input_seq") or 0) > 0:
            return {
                "created": False,
                "skipped": True,
                "reason": "already_has_input_checkpoint",
                "head": self._get_head_tree(),
            }

        previous_head = self._get_head_tree()
        t_stage = time.perf_counter()
        self._stage_all()
        perf_log("versioning baseline stage_all", elapsed_ms=(time.perf_counter() - t_stage) * 1000)
        t_diff = time.perf_counter()
        current_head = self._get_head_tree()
        perf_log("versioning baseline write_tree", elapsed_ms=(time.perf_counter() - t_diff) * 1000)
        # 通过 tree-to-tree diff 判断是否有变更
        _, numstat_text, _ = self._run_git(
            ["diff", "--numstat", previous_head or self.EMPTY_TREE_HASH, current_head],
            check=False,
        )
        has_changes = bool((numstat_text or "").strip())
        meta["last_tree_hash"] = current_head
        self.save_meta(meta)
        return {
            "created": bool(has_changes),
            "skipped": False,
            "reason": "baseline_committed" if has_changes else "baseline_unchanged",
            "head": current_head,
            "parent_tree_hash": previous_head,
            "has_changes": has_changes,
        }

    # ----------------------------
    # diff / checkpoint
    # ----------------------------
    def _parse_numstat(self, text: str) -> Dict[str, Dict[str, int]]:
        stats: Dict[str, Dict[str, int]] = {}
        for line in (text or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            ins_raw, del_raw = parts[0], parts[1]
            path = self._decode_git_path(parts[2])
            ins = 0 if ins_raw == "-" else int(ins_raw or 0)
            dele = 0 if del_raw == "-" else int(del_raw or 0)
            stats[path] = {"insertions": ins, "deletions": dele}
        return stats

    def _build_diff_summary(self, parent_tree_hash: Optional[str], tree_hash: str) -> Dict[str, Any]:
        base = parent_tree_hash or self.EMPTY_TREE_HASH
        _, numstat_text, _ = self._run_git(["diff", "--numstat", base, tree_hash], check=False)
        _, status_text, _ = self._run_git(["diff", "--name-status", base, tree_hash], check=False)

        numstats = self._parse_numstat(numstat_text)
        files: List[Dict[str, Any]] = []
        total_insertions = 0
        total_deletions = 0
        for raw in (status_text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            parts = line.split("\t")
            code = parts[0] if parts else ""
            path = self._decode_git_path(parts[-1] if len(parts) >= 2 else "")
            old_path = self._decode_git_path(parts[1]) if code.startswith("R") and len(parts) >= 3 else None
            file_stat = numstats.get(path, {"insertions": 0, "deletions": 0})
            ins = int(file_stat.get("insertions") or 0)
            dele = int(file_stat.get("deletions") or 0)
            total_insertions += ins
            total_deletions += dele
            files.append(
                {
                    "path": path,
                    "status": code,
                    "old_path": old_path,
                    "insertions": ins,
                    "deletions": dele,
                }
            )
        return {
            "insertions": total_insertions,
            "deletions": total_deletions,
            "files_changed": len(files),
            "files": files,
        }

    def create_checkpoint(
        self,
        *,
        message: str,
        message_index: int,
        workspace_path: Optional[str] = None,
        conversation_snapshot: Optional[Dict[str, Any]] = None,
        run_status: Optional[str] = None,
        tracking_mode: Optional[str] = None,
        diff_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        meta = self.load_meta()
        normalized_tracking_mode = self.normalize_tracking_mode(
            tracking_mode or meta.get("tracking_mode")
        )
        previous_head: Optional[str] = self._get_head_tree()
        current_head: Optional[str] = previous_head

        if normalized_tracking_mode == self.TRACKING_MODE_WORKSPACE_AND_CONVERSATION:
            self.ensure_repo()
            previous_head = self._get_head_tree()
            self._stage_all()
            current_head = self._get_head_tree()
            if not current_head:
                raise VersioningError("创建版本快照失败：未获取到 tree hash")
            # 通过 tree-to-tree diff 判断是否有变更
            _, numstat_text, _ = self._run_git(
                ["diff", "--numstat", previous_head or self.EMPTY_TREE_HASH, current_head],
                check=False,
            )
            has_changes = bool((numstat_text or "").strip())

        if diff_summary is not None:
            diff_summary = dict(diff_summary)
        elif (
            normalized_tracking_mode == self.TRACKING_MODE_WORKSPACE_AND_CONVERSATION
            and current_head != previous_head
        ):
            diff_summary = self._build_diff_summary(previous_head, current_head)
        else:
            diff_summary = {"insertions": 0, "deletions": 0, "files_changed": 0, "files": []}

        seq = int(meta.get("last_input_seq") or 0) + 1
        row = {
            "seq": seq,
            "message": (message or "").strip(),
            "message_preview": ((message or "").strip().splitlines() or [""])[0][:200],
            "message_index": int(message_index),
            "timestamp": datetime.now().isoformat(),
            "workspace_path": str(workspace_path or ""),
            "tree_hash": current_head,
            "parent_tree_hash": previous_head,
            "insertions": int(diff_summary.get("insertions") or 0),
            "deletions": int(diff_summary.get("deletions") or 0),
            "files_changed": int(diff_summary.get("files_changed") or 0),
            "files": diff_summary.get("files") or [],
            "changed": bool(
                normalized_tracking_mode == self.TRACKING_MODE_WORKSPACE_AND_CONVERSATION
                and current_head != previous_head
            ),
            "tracking_mode": normalized_tracking_mode,
        }
        if isinstance(conversation_snapshot, dict):
            row["snapshot_file"] = self._write_snapshot(seq, conversation_snapshot)
            shallow_message_id = conversation_snapshot.get("shallow_message_id")
            if shallow_message_id:
                row["shallow_message_id"] = str(shallow_message_id)
        if run_status:
            row["run_status"] = str(run_status)
        self._append_input(row)
        meta["last_input_seq"] = seq
        meta["last_tree_hash"] = current_head
        meta["tracking_mode"] = normalized_tracking_mode
        self.save_meta(meta)
        return row

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        rows = self._read_inputs()
        rows.sort(key=lambda item: int(item.get("seq") or 0), reverse=True)
        return rows

    def get_checkpoint(self, seq: int) -> Optional[Dict[str, Any]]:
        for row in self._read_inputs():
            if int(row.get("seq") or 0) == int(seq):
                return row
        return None

    def _collect_file_patch_lines(
        self,
        *,
        parent_tree_hash: Optional[str],
        tree_hash: str,
        path: str,
        old_path: Optional[str] = None,
        max_lines: int = 600,
    ) -> Dict[str, Any]:
        target_paths: List[str] = []
        for candidate in [old_path, path]:
            value = str(candidate or "").strip()
            if value and value not in target_paths:
                target_paths.append(value)
        if not target_paths:
            return {"lines": [], "truncated": False}

        base = parent_tree_hash or self.EMPTY_TREE_HASH
        _, patch_text, _ = self._run_git(
            ["diff", "--no-color", "--unified=200", base, tree_hash, "--", *target_paths],
            check=False,
        )
        parsed = self._extract_contextual_patch_lines(patch_text or "", max_lines=max_lines)
        lines = parsed.get("lines") or []
        truncated = bool(parsed.get("truncated"))
        # 回退策略：若按路径过滤拿不到行，但该文件确实有增删，尝试从整次 commit patch 中按文件段提取。
        if not lines:
            fallback = self._collect_file_patch_lines_from_full_diff(
                parent_tree_hash=parent_tree_hash,
                tree_hash=tree_hash,
                target_paths=target_paths,
                max_lines=max_lines,
            )
            lines = fallback.get("lines") or []
            truncated = bool(fallback.get("truncated")) or truncated
        return {"lines": lines, "truncated": truncated}

    def _collect_file_patch_lines_from_full_diff(
        self,
        *,
        parent_tree_hash: Optional[str],
        tree_hash: str,
        target_paths: List[str],
        max_lines: int = 600,
    ) -> Dict[str, Any]:
        base = parent_tree_hash or self.EMPTY_TREE_HASH
        _, patch_text, _ = self._run_git(
            ["diff", "--no-color", "--unified=200", base, tree_hash],
            check=False,
        )

        targets = {str(p or "").lstrip("./") for p in target_paths if str(p or "").strip()}
        chunk_lines: List[str] = []
        lines: List[Dict[str, Any]] = []
        truncated = False
        in_target_section = False
        for raw in (patch_text or "").splitlines():
            if raw.startswith("diff --git "):
                if in_target_section and chunk_lines and len(lines) < max_lines:
                    parsed_chunk = self._extract_contextual_patch_lines("\n".join(chunk_lines), max_lines=max_lines - len(lines))
                    lines.extend(parsed_chunk.get("lines") or [])
                    truncated = truncated or bool(parsed_chunk.get("truncated"))
                    chunk_lines = []
                in_target_section = False
                try:
                    parts = raw.split(" ")
                    a_path = self._decode_git_path((parts[2] or "").replace("a/", "", 1)).lstrip("./")
                    b_path = self._decode_git_path((parts[3] or "").replace("b/", "", 1)).lstrip("./")
                    if a_path in targets or b_path in targets:
                        in_target_section = True
                except Exception:
                    in_target_section = False
                continue
            if not in_target_section:
                continue
            chunk_lines.append(raw)
            if len(lines) >= max_lines:
                truncated = True
                break
        if in_target_section and chunk_lines and len(lines) < max_lines and not truncated:
            parsed_chunk = self._extract_contextual_patch_lines("\n".join(chunk_lines), max_lines=max_lines - len(lines))
            lines.extend(parsed_chunk.get("lines") or [])
            truncated = truncated or bool(parsed_chunk.get("truncated"))
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            truncated = True
        return {"lines": lines, "truncated": truncated}

    def get_checkpoint_detail(self, seq: int, *, include_patch: bool = True) -> Optional[Dict[str, Any]]:
        row = self.get_checkpoint(seq)
        if not row:
            return None
        payload = dict(row)
        files = payload.get("files")
        if not include_patch or not isinstance(files, list):
            return payload
        parent_tree_hash = payload.get("parent_tree_hash")
        tree_hash = payload.get("tree_hash")
        normalized_files: List[Dict[str, Any]] = []
        for file_item in files:
            if not isinstance(file_item, dict):
                continue
            item = dict(file_item)
            if tree_hash and str(item.get("path") or "").strip():
                patch = self._collect_file_patch_lines(
                    parent_tree_hash=parent_tree_hash,
                    tree_hash=tree_hash,
                    path=str(item.get("path") or ""),
                    old_path=item.get("old_path"),
                )
                item["patch_lines"] = patch.get("lines") or []
                item["patch_truncated"] = bool(patch.get("truncated"))
            else:
                item["patch_lines"] = []
                item["patch_truncated"] = False
            normalized_files.append(item)
        payload["files"] = normalized_files
        return payload

    def get_latest_checkpoint(self) -> Optional[Dict[str, Any]]:
        rows = self.list_checkpoints()
        return rows[0] if rows else None

    def prune_checkpoints_after(self, seq: int) -> Dict[str, Any]:
        target = int(seq)
        rows = self._read_inputs()
        if not rows:
            return {"kept": 0, "removed": 0, "max_seq": 0}

        kept_rows = [row for row in rows if int(row.get("seq") or 0) <= target]
        removed_rows = [row for row in rows if int(row.get("seq") or 0) > target]

        for row in removed_rows:
            rel = row.get("snapshot_file")
            if not rel:
                continue
            try:
                path = (self.paths.save_root / str(rel)).resolve()
                if path.exists() and path.is_file():
                    path.unlink()
            except Exception:
                continue

        self._write_inputs(kept_rows)
        max_seq = max((int(r.get("seq") or 0) for r in kept_rows), default=0)
        latest_kept = None
        for r in kept_rows:
            if int(r.get("seq") or 0) == max_seq:
                latest_kept = r
        meta = self.load_meta()
        meta["last_input_seq"] = max_seq
        meta["last_tree_hash"] = (latest_kept or {}).get("tree_hash") or self._get_head_tree()
        self.save_meta(meta)
        return {
            "kept": len(kept_rows),
            "removed": len(removed_rows),
            "max_seq": max_seq,
            "last_tree_hash": meta.get("last_tree_hash"),
        }

    # ----------------------------
    # restore / status
    # ----------------------------
    def restore_to_tree(self, tree_hash: str) -> Dict[str, Any]:
        if not tree_hash:
            raise VersioningError("缺少 tree hash")
        self.ensure_repo()
        ok, _, _ = self._run_git(["cat-file", "-e", tree_hash], check=False)
        if not ok:
            raise VersioningError("目标 tree 不存在")
        # 使用 read-tree + checkout-index 恢复到指定 tree，不依赖 commit 历史
        self._run_git(["read-tree", tree_hash])
        self._run_git(["checkout-index", "-a", "-f"])
        self._run_git(["clean", "-fd"])
        meta = self.load_meta()
        meta["last_restored_tree_hash"] = tree_hash
        meta["last_tree_hash"] = tree_hash
        self.save_meta(meta)
        return {"success": True, "tree_hash": tree_hash}

    def detect_mismatch(
        self,
        latest_tree_hash: Optional[str],
        expected_workspace_path: Optional[str] = None,
        *,
        tracking_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_tracking_mode = self.normalize_tracking_mode(tracking_mode)
        if normalized_tracking_mode == self.TRACKING_MODE_CONVERSATION_ONLY:
            return {
                "has_repo": (self.paths.git_dir / "HEAD").exists(),
                "head": self._get_head_tree(),
                "dirty": False,
                "mismatch": False,
                "workspace_matched": True,
            }
        expected = str(expected_workspace_path or "").strip()
        current = str(self.project_path)
        workspace_matched = True if not expected else (Path(expected).expanduser().resolve() == self.project_path)
        if not (self.paths.git_dir / "HEAD").exists():
            return {
                "has_repo": False,
                "head": None,
                "dirty": False,
                "mismatch": False,
                "workspace_matched": workspace_matched,
            }
        if not workspace_matched:
            return {
                "has_repo": True,
                "head": self._get_head_tree(),
                "dirty": False,
                "mismatch": False,
                "workspace_matched": False,
                "expected_workspace_path": expected,
                "current_workspace_path": current,
            }
        head = self._get_head_tree()
        dirty = not self._is_clean()
        mismatch = bool(dirty or (latest_tree_hash and head and latest_tree_hash != head))
        return {
            "has_repo": True,
            "head": head,
            "dirty": dirty,
            "mismatch": mismatch,
            "workspace_matched": True,
            "expected_workspace_path": expected or current,
            "current_workspace_path": current,
        }
    def _extract_contextual_patch_lines(self, patch_text: str, max_lines: int = 600) -> Dict[str, Any]:
        """
        Extract +/- lines plus 3 context lines before and after changed regions.
        """
        lines: List[Dict[str, Any]] = []
        truncated = False
        pre_context: deque[str] = deque(maxlen=3)
        post_context_needed = 0

        for raw in (patch_text or "").splitlines():
            if raw.startswith("diff --git "):
                pre_context.clear()
                post_context_needed = 0
                continue
            if raw.startswith("index ") or raw.startswith("--- ") or raw.startswith("+++ "):
                continue
            if raw.startswith("@@"):
                pre_context.clear()
                post_context_needed = 0
                continue

            if not raw:
                prefix = " "
                content = ""
            else:
                prefix = raw[0]
                content = raw[1:] if len(raw) > 1 else ""

            if prefix == " ":
                if post_context_needed > 0:
                    lines.append({"type": "context", "content": content})
                    post_context_needed -= 1
                else:
                    pre_context.append(content)
            elif prefix in {"+", "-"}:
                if pre_context:
                    for ctx in pre_context:
                        lines.append({"type": "context", "content": ctx})
                    pre_context.clear()
                lines.append({"type": "add" if prefix == "+" else "remove", "content": content})
                post_context_needed = 3
            else:
                continue

            if len(lines) >= max_lines:
                truncated = True
                break

        return {"lines": lines[:max_lines], "truncated": truncated}
