# utils/conversation_manager.py - 对话持久化管理器（集成Token统计）

import json
import os
import time
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from utils.atomic_io import replace_with_retry
from utils.log_rotation import append_line
try:
    from config import DATA_DIR, HOST_WORKSPACES_FILE, LOGS_DIR
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import DATA_DIR, HOST_WORKSPACES_FILE, LOGS_DIR

try:
    from utils.perf_log import perf_log
except Exception:
    perf_log = None

from modules.i18n import tr

@dataclass
class ConversationMetadata:
    """对话元数据"""
    id: str
    title: str
    created_at: str
    updated_at: str
    project_path: Optional[str]
    project_relative_path: Optional[str]
    thinking_mode: bool
    total_messages: int
    total_tools: int
    run_mode: str = "fast"
    model_key: Optional[str] = None
    has_images: bool = False
    has_videos: bool = False
    status: str = "active"  # active, archived, error


class IndexMixin:
    """ConversationManager index mixin 能力 mixin。"""

    def _migrate_legacy_conversation_files(self):
        """把 data/conversations/conv_*.json 按 metadata.project_path 移入对应 workspace 子目录。"""
        try:
            legacy_files = [
                p for p in self.conversations_root.glob("conv_*.json")
                if p.is_file() and p.parent == self.conversations_root
            ]
        except Exception:
            return

        moved_count = 0
        for file_path in legacy_files:
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
                project_path = self._normalize_path(metadata.get("project_path"))
                workspace_id = self._resolve_workspace_id(project_path)
                if not workspace_id:
                    continue
                target_dir = self._conversation_dir_for_workspace(workspace_id)
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / file_path.name
                if target.exists():
                    print(f"⚠️ legacy 对话迁移跳过，目标已存在: {target}")
                    continue
                file_path.replace(target)
                moved_count += 1
            except Exception as exc:
                print(f"⚠️ legacy 对话迁移失败 {file_path.name}: {exc}")
        if moved_count:
            print(f"🔄 已迁移 {moved_count} 个 legacy 对话到工作区目录")

    def _iter_conversation_files(self, sort_by_mtime: bool = True):
        """遍历对话文件（排除索引文件），可按修改时间降序排序。"""
        files = []
        for p in self.conversations_dir.glob("*.json"):
            if p == self.index_file:
                continue
            stem = p.stem or ""
            # 跳过索引备份/损坏文件，避免被误当作对话文件参与重建
            if stem == "index" or stem.startswith("index_corrupt_"):
                continue
            # 仅纳入标准对话文件，避免其他 json 干扰索引
            if not stem.startswith("conv_"):
                continue
            files.append(p)
        if sort_by_mtime:
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files

    def _atomic_write_json(self, target_file: Path, payload: Dict[str, Any]):
        """原子写入 JSON：使用唯一临时文件，避免并发写同名 .tmp 产生竞态。"""
        target_file.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(target_file.parent),
                prefix=f".{target_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as fh:
                temp_path = Path(fh.name)
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            # Windows 下目标文件被并发读取/杀软扫描短暂持锁时 replace 会抛
            # WinError 5/32，replace_with_retry 做短退避重试（POSIX 行为不变）
            try:
                replace_with_retry(temp_path, target_file)
            except OSError:
                # 重试预算耗尽仍失败：把已写好的新内容转移为 .last_failed.tmp 留证，
                # 便于事后恢复与频率统计（固定文件名覆盖，不累积垃圾）；原异常继续抛出
                try:
                    orphan = target_file.with_name(f".{target_file.name}.last_failed.tmp")
                    replace_with_retry(temp_path, orphan)
                    temp_path = None  # 已转移，finally 不再删除
                except OSError:
                    pass
                raise
        finally:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass

    def _rebuild_index_from_files(self, max_count: Optional[int] = None) -> Dict:
        """
        从现有对话文件重建索引。

        Args:
            max_count: 限制重建的条目数（按文件修改时间倒序）；None 表示全量重建。
        """
        t0 = time.perf_counter()
        rebuilt_index: Dict[str, Dict] = {}
        files = self._iter_conversation_files(sort_by_mtime=True)
        if max_count is not None:
            files = files[:max(0, int(max_count))]
        if perf_log:
            perf_log("_rebuild_index_from_files start", extra={"file_count": len(files), "max_count": max_count})
        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                    if not raw:
                        continue
                    data = json.loads(raw)
            except Exception as exc:
                print(f"⚠️ 重建索引时跳过 {file_path.name}: {exc}")
                continue

            conv_id = data.get("id") or file_path.stem
            metadata = data.get("metadata", {}) or {}

            rebuilt_index[conv_id] = {
                "title": data.get("title") or tr("conv_mgr.untitled_conversation"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "project_path": metadata.get("project_path"),
                "project_relative_path": metadata.get("project_relative_path"),
                "thinking_mode": metadata.get("thinking_mode", False),
                "run_mode": metadata.get("run_mode") or ("thinking" if metadata.get("thinking_mode") else "fast"),
                "model_key": metadata.get("model_key"),
                "has_images": metadata.get("has_images", False),
                "has_videos": metadata.get("has_videos", False),
                "total_messages": metadata.get("total_messages", 0),
                "total_tools": metadata.get("total_tools", 0),
                "status": metadata.get("status", "active"),
                "multi_agent_mode": bool(metadata.get("multi_agent_mode", False)),
            }
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if perf_log:
            perf_log("_rebuild_index_from_files done", elapsed_ms=elapsed_ms, extra={"rebuilt_size": len(rebuilt_index), "max_count": max_count})
        if rebuilt_index:
            print(f"🔄 已从对话文件重建索引，共 {len(rebuilt_index)} 条记录")
        return rebuilt_index

    def _index_missing_conversations(self, index: Dict) -> bool:
        """检查索引是否缺失本地对话文件"""
        t0 = time.perf_counter()
        index_ids = set(index.keys())
        files = list(self._iter_conversation_files())
        missing = False
        for file_path in files:
            conv_id = file_path.stem
            if conv_id and conv_id not in index_ids:
                print(f"🔍 对话 {conv_id} 未出现在索引中，将重建索引。")
                missing = True
                break
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if perf_log:
            perf_log("_index_missing_conversations", elapsed_ms=elapsed_ms, extra={"file_count": len(files), "index_size": len(index), "missing": missing})
        return missing

    def _load_index(self, ensure_integrity: bool = False, max_rebuild: Optional[int] = None) -> Dict:
        """加载对话索引，可选地在缺失时自动重建（可限制重建条数）"""
        try:
            index: Dict = {}
            if self.index_file.exists():
                # 只在 with 块内读字节：json.loads 期间不持有文件句柄，避免
                # Windows 下读取方持锁阻塞写方 os.replace（无 FILE_SHARE_DELETE）
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                if content:
                    index = json.loads(content)
                    if index:
                        if ensure_integrity and not self._index_verified:
                            if self._index_missing_conversations(index):
                                rebuilt = self._rebuild_index_from_files(max_count=max_rebuild)
                                if rebuilt:
                                    self._save_index(rebuilt)
                                    index = rebuilt
                            self._index_verified = True
                        return index
                    # 索引为空但对话文件仍然存在时尝试重建
                    rebuilt = self._rebuild_index_from_files(max_count=max_rebuild)
                    if rebuilt:
                        self._save_index(rebuilt)
                        if ensure_integrity:
                            self._index_verified = True
                        return rebuilt
                    return {}
            # 索引缺失但存在对话文件时重建
            rebuilt = self._rebuild_index_from_files(max_count=max_rebuild)
            if rebuilt:
                self._save_index(rebuilt)
                if ensure_integrity:
                    self._index_verified = True
                return rebuilt
            return index
        except (json.JSONDecodeError, Exception) as e:
            print(f"⚠️ 加载对话索引失败，将尝试重建: {e}")
            backup_path = self.index_file.with_name(
                f"{self.index_file.stem}_corrupt_{int(time.time() * 1000)}{self.index_file.suffix}"
            )
            try:
                if self.index_file.exists():
                    self.index_file.replace(backup_path)
                    print(f"🗄️ 已备份损坏的索引文件到: {backup_path.name}")
            except Exception as backup_exc:
                print(f"⚠️ 备份损坏索引文件失败: {backup_exc}")
            # 索引损坏场景优先全量重建，避免仅重建部分导致“对话丢失”错觉
            rebuilt = self._rebuild_index_from_files(max_count=None)
            if rebuilt:
                self._save_index(rebuilt)
                if ensure_integrity:
                    self._index_verified = True
                return rebuilt
            return {}

    def _save_index(self, index: Dict):
        """保存对话索引"""
        try:
            with self._io_lock:
                self._atomic_write_json(self.index_file, index)
        except Exception as e:
            print(f"⌘ 保存对话索引失败: {e}")
            # 落盘留证（线程安全、自动轮转、异常自吞），便于统计失败频率与诊断
            append_line(
                Path(LOGS_DIR) / "conversation_index_failures.log",
                json.dumps({
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "index_file": str(self.index_file),
                    "error": str(e),  # str 含 [WinError N] 与源/目标路径，repr 会丢失
                }, ensure_ascii=False),
            )

    def _ensure_index_covering(self, limit: int, offset: int) -> Dict:
        """
        确保索引涵盖到 offset+limit 条记录，不足时按需扩展重建（仍按 mtime 倒序，增量加载批量）。
        每次先检查是否有新增对话文件未被纳入索引，避免"创建后列表不显示"的问题。
        """
        t0 = time.perf_counter()
        needed = max(0, int(offset) + int(limit))
        index = self._load_index()
        load_ms = (time.perf_counter() - t0) * 1000
        if perf_log:
            perf_log("_ensure_index_covering load_index", elapsed_ms=load_ms, extra={"index_size": len(index), "needed": needed})

        # 主动检查新增文件：索引可能滞后于实际对话文件，导致列表不同步。
        t1 = time.perf_counter()
        missing = self._index_missing_conversations(index)
        missing_ms = (time.perf_counter() - t1) * 1000
        if perf_log:
            perf_log("_ensure_index_covering check_missing", elapsed_ms=missing_ms, extra={"missing": missing, "index_size": len(index)})
        if missing:
            t2 = time.perf_counter()
            rebuilt = self._rebuild_index_from_files(max_count=max(needed, len(index) + 10))
            rebuild_ms = (time.perf_counter() - t2) * 1000
            if perf_log:
                perf_log("_ensure_index_covering rebuild_missing", elapsed_ms=rebuild_ms, extra={"rebuilt_size": len(rebuilt) if rebuilt else 0})
            if rebuilt:
                self._save_index(rebuilt)
                index = rebuilt

        # 关键：覆盖目标不应超过实际文件总数。此前只和 needed(offset+limit) 比较，
        # 当 needed 大于对话总数时会每次触发 rebuild_needed + rebuild_full 两次全量重建，
        # 日志刷屏且随对话数线性变慢。
        try:
            total_files = len(self._iter_conversation_files(sort_by_mtime=False))
        except Exception:
            total_files = None
        coverage_target = needed if total_files is None else min(needed, total_files)

        if len(index) >= coverage_target:
            total_ms = (time.perf_counter() - t0) * 1000
            if perf_log:
                perf_log("_ensure_index_covering return", elapsed_ms=total_ms, extra={"index_size": len(index), "needed": needed, "total_files": total_files})
            return index

        # 第一次尝试：扩展到需要的数量（按更新时间倒序）
        t3 = time.perf_counter()
        rebuilt = self._rebuild_index_from_files(max_count=needed)
        rebuild_ms = (time.perf_counter() - t3) * 1000
        if perf_log:
            perf_log("_ensure_index_covering rebuild_needed", elapsed_ms=rebuild_ms, extra={"rebuilt_size": len(rebuilt) if rebuilt else 0})
        if rebuilt:
            self._save_index(rebuilt)
            index = rebuilt

        # 如果仍不足且存在更多文件可能未被纳入（例如首批限定过小），进行一次全量重建兜底
        if len(index) < needed:
            t4 = time.perf_counter()
            rebuilt_full = self._rebuild_index_from_files(max_count=None)
            rebuild_full_ms = (time.perf_counter() - t4) * 1000
            if perf_log:
                perf_log("_ensure_index_covering rebuild_full", elapsed_ms=rebuild_full_ms, extra={"rebuilt_size": len(rebuilt_full) if rebuilt_full else 0})
            if rebuilt_full:
                self._save_index(rebuilt_full)
                index = rebuilt_full

        total_ms = (time.perf_counter() - t0) * 1000
        if perf_log:
            perf_log("_ensure_index_covering return", elapsed_ms=total_ms, extra={"index_size": len(index), "needed": needed})
        return index
