"""上传隔离、扫描与审计工具。"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from config import (
    MAX_UPLOAD_SIZE,
    UPLOAD_ALLOWED_EXTENSIONS,
    UPLOAD_CLAMAV_ARGS,
    UPLOAD_CLAMAV_BIN,
    UPLOAD_CLAMAV_ENABLED,
    UPLOAD_CLAMAV_TIMEOUT_SECONDS,
    UPLOAD_SCAN_LOG_SUBDIR,
)
from utils.logger import setup_logger

if TYPE_CHECKING:
    from modules.user_manager import UserWorkspace
    from werkzeug.datastructures import FileStorage


class UploadSecurityError(Exception):
    """上传被拒绝或扫描失败时抛出的业务异常。"""

    def __init__(self, message: str, code: str = "upload_error"):
        super().__init__(message)
        self.code = code


@dataclass
class StageRecord:
    """暂存区中的上传文件快照。"""

    path: Path
    filename: str
    size: int
    sha256: str
    mime: Optional[str]


@dataclass
class ScanReport:
    """病毒扫描结果。"""

    status: str
    engine: str
    signature: Optional[str] = None
    message: Optional[str] = None
    duration_ms: Optional[int] = None

    @property
    def is_clean(self) -> bool:
        return self.status in {"passed", "skipped"}


class UploadQuarantineManager:
    """负责将上传文件落地到隔离区、执行安全扫描并记录审计日志。"""

    def __init__(self, workspace: "UserWorkspace", *, logger_name: Optional[str] = None):
        self.workspace = workspace
        self.quarantine_dir = Path(workspace.quarantine_dir).resolve()
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        log_rel = Path(UPLOAD_SCAN_LOG_SUBDIR) / f"{workspace.username}.log"
        self.logger = setup_logger(
            logger_name or f"upload_guard.{workspace.username}",
            str(log_rel),
        )
        self.allowed_extensions = tuple(UPLOAD_ALLOWED_EXTENSIONS)
        self.clamav_enabled = bool(UPLOAD_CLAMAV_ENABLED)
        self.clamav_bin = self._resolve_clamav_bin(UPLOAD_CLAMAV_BIN)
        self.clamav_args = list(UPLOAD_CLAMAV_ARGS)
        self.clamav_timeout = int(max(1, UPLOAD_CLAMAV_TIMEOUT_SECONDS))

    def process_upload(
        self,
        file_obj: "FileStorage",
        target_path: Path,
        *,
        username: str,
        source: str,
        original_name: str,
        relative_path: str,
    ) -> Dict[str, Any]:
        """保存文件到隔离区、执行扫描并将通过的文件同步到工作目录。"""
        stage = self._stage_file(file_obj, original_name)
        metadata: Dict[str, Any] = {
            "upload_id": uuid.uuid4().hex,
            "username": username,
            "source": source,
            "original_name": original_name,
            "target_path": str(target_path),
            "target_relative": relative_path,
            "staged_path": str(stage.path),
            "size": stage.size,
            "sha256": stage.sha256,
            "mime": stage.mime,
        }
        scan_report: Optional[ScanReport] = None
        promoted_path: Optional[Path] = None

        try:
            self._enforce_size(stage.size)
            self._enforce_extension(target_path.name)
            scan_report = self._scan(stage.path)
            metadata["scan"] = asdict(scan_report)
            if not scan_report.is_clean:
                metadata["scan_failure_reason"] = scan_report.message or scan_report.signature
                raise UploadSecurityError("安全审核未通过", code="scan_failed")
            promoted_path = self._promote(stage.path, target_path)
            metadata["final_path"] = str(promoted_path)
            self._log_event(True, metadata)
            return {
                "final_path": promoted_path,
                "metadata": metadata,
            }
        except UploadSecurityError as exc:
            metadata["error"] = {
                "code": exc.code,
                "message": str(exc),
            }
            if scan_report:
                metadata.setdefault("scan", asdict(scan_report))
            self._log_event(False, metadata)
            raise
        finally:
            if stage.path.exists():
                try:
                    stage.path.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _stage_file(self, file_obj: "FileStorage", original_name: str) -> StageRecord:
        suffix = "".join(Path(original_name or "").suffixes[-2:]) or Path(original_name or "").suffix
        unique_name = f"{int(time.time())}_{uuid.uuid4().hex}{suffix or ''}"
        staged_path = self.quarantine_dir / unique_name
        file_obj.save(staged_path)
        size = staged_path.stat().st_size
        sha256 = self._hash_file(staged_path)
        mime, _ = mimetypes.guess_type(original_name or "")
        return StageRecord(staged_path, original_name, size, sha256, mime)

    def _enforce_size(self, size: int):
        if size > MAX_UPLOAD_SIZE:
            raise UploadSecurityError(
                f"文件大小 {size} 超过上限 {MAX_UPLOAD_SIZE} 字节",
                code="size_exceeded",
            )

    def _enforce_extension(self, filename: str):
        if not self.allowed_extensions:
            return
        lowered = (filename or "").lower()
        for pattern in self.allowed_extensions:
            if lowered.endswith(pattern):
                return
        raise UploadSecurityError("文件类型不在允许列表中", code="extension_forbidden")

    def _scan(self, staged_path: Path) -> ScanReport:
        if not self.clamav_enabled:
            return ScanReport(status="skipped", engine="clamdscan", message="已跳过病毒扫描")
        if not self.clamav_bin:
            raise UploadSecurityError("未找到 ClamAV 扫描器，请检查配置", code="scanner_missing")

        command = [self.clamav_bin] + self.clamav_args + [str(staged_path)]
        start = time.time()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.clamav_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ScanReport(
                status="error",
                engine="clamdscan",
                message="扫描超时",
                duration_ms=int((time.time() - start) * 1000),
            )
        except FileNotFoundError as exc:
            raise UploadSecurityError("ClamAV 扫描器不可用", code="scanner_missing") from exc

        duration_ms = int((time.time() - start) * 1000)
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode == 0:
            return ScanReport(status="passed", engine="clamdscan", duration_ms=duration_ms)

        if result.returncode == 1:
            signature = self._extract_signature(stdout) or self._extract_signature(stderr)
            message = signature or "检测到可疑内容"
            return ScanReport(
                status="failed",
                engine="clamdscan",
                signature=signature,
                message=message,
                duration_ms=duration_ms,
            )

        message = stderr or stdout or f"扫描失败（退出码 {result.returncode}）"
        return ScanReport(
            status="error",
            engine="clamdscan",
            message=message,
            duration_ms=duration_ms,
        )

    def _promote(self, staged_path: Path, target_path: Path) -> Path:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged_path), str(target_path))
        return target_path

    def _hash_file(self, path: Path) -> str:
        sha256 = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _extract_signature(self, text: str) -> Optional[str]:
        if not text:
            return None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or "FOUND" not in line.upper():
                continue
            parts = line.split(":", 1)
            tail = parts[-1].strip()
            if tail.upper().endswith("FOUND"):
                signature = tail[: -len("FOUND")].strip()
                if signature:
                    return signature
        return None

    def _resolve_clamav_bin(self, candidate: str) -> Optional[str]:
        if not candidate:
            return None
        candidate = candidate.strip()
        if not candidate:
            return None
        if os.path.isabs(candidate):
            return candidate if Path(candidate).exists() else None
        resolved = shutil.which(candidate)
        return resolved or None

    def _log_event(self, accepted: bool, payload: Dict[str, Any]):
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "accepted": accepted,
            **payload,
        }
        try:
            self.logger.info("UPLOAD_AUDIT %s", json.dumps(entry, ensure_ascii=False))
        except Exception:
            self.logger.warning("无法写入上传审计日志：%s", entry)


__all__ = [
    "UploadQuarantineManager",
    "UploadSecurityError",
]
