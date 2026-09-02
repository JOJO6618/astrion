"""API 专用用户与工作区管理（JSON + Bearer Token 哈希）。

支持 API 用户的创建/删除、Token 持久化（哈希 + 加密回显）与基础用量计数。
"""

from __future__ import annotations
import json
import hashlib
import threading
import secrets
import base64
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

from config import (
    API_USER_SPACE_DIR,
    API_USERS_DB_FILE,
    API_TOKENS_FILE,
    API_USAGE_FILE,
    API_TOKEN_SECRET,
)
from modules.personalization_manager import ensure_personalization_config
try:
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore
except Exception:  # pragma: no cover - 环境缺失时给予友好提示
    Fernet = None
    InvalidToken = Exception  # type: ignore

from modules.i18n import tr


@dataclass
class ApiUserRecord:
    username: str
    token_sha256: str
    created_at: str
    note: str = ""


@dataclass
class ApiUserWorkspace:
    """API 用户的单个工作区描述。"""
    username: str
    workspace_id: str
    root: Path
    project_path: Path
    data_dir: Path           # 会话/备份等落盘到这里（每个工作区独立）
    logs_dir: Path
    uploads_dir: Path        # project/.astrion/user_upload
    quarantine_dir: Path     # 上传隔离区（按用户/工作区划分）
    shared_dir: Path         # 用户级共享目录（prompts/personalization）
    prompts_dir: Path        # 实际使用的 prompts 目录（指向 shared_dir/prompt）
    personalization_dir: Path  # 实际使用的 personalization 目录（指向 shared_dir/personalization）


class ApiUserManager:
    """最小化的 API 用户管理：只校验 token 哈希并准备隔离工作区。"""

    def __init__(
        self,
        users_file: str = API_USERS_DB_FILE,
        tokens_file: str = API_TOKENS_FILE,
        workspace_root: str = API_USER_SPACE_DIR,
        usage_file: str = API_USAGE_FILE,
    ):
        self.users_file = Path(users_file)
        self.tokens_file = Path(tokens_file)
        self.usage_file = Path(usage_file)
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        self._users: Dict[str, ApiUserRecord] = {}
        self._tokens: Dict[str, Dict[str, Any]] = {}
        self._usage: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

        self._load_users()
        self._load_tokens()
        self._load_usage()

    # ----------------------- public APIs -----------------------
    def list_users(self) -> Dict[str, ApiUserRecord]:
        with self._lock:
            return dict(self._users)

    def get_user_by_token(self, bearer_token: str) -> Optional[ApiUserRecord]:
        if not bearer_token:
            return None
        token_sha = self._sha256(bearer_token)
        with self._lock:
            for user in self._users.values():
                if user.token_sha256 == token_sha:
                    return user
        return None

    def ensure_workspace(self, username: str, workspace_id: str = "default") -> ApiUserWorkspace:
        """为 API 用户创建/获取指定工作区。

        目录布局（每个用户）：
        <root>/<username>/
          shared/               # 用户级共享（prompts/personalization）
            prompts/
            personalization/
          workspaces/<ws>/      # 单个工作区
            project/
              .astrion/user_upload/
            data/
              conversations/
              backups/
            logs/
        """
        username = username.strip().lower()
        ws_id = (workspace_id or "default").strip()
        if not ws_id:
            ws_id = "default"
        # 安全：workspace_id 参与路径拼接，必须严格白名单（防 `..`/斜杠穿越）
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", ws_id) or ".." in ws_id:
            raise ValueError(tr("api_user_mgr.invalid_workspace_id"))

        user_root = (self.workspace_root / username).resolve()
        shared_dir = user_root / "shared"
        prompts_dir = shared_dir / "prompts"
        personalization_dir = shared_dir / "personalization"
        work_root = user_root / "workspaces" / ws_id

        project_path = work_root / "project"
        data_dir = work_root / "data"
        logs_dir = work_root / "logs"
        uploads_dir = project_path / ".astrion" / "user_upload"
        skills_dir = project_path / ".astrion" / "skills"

        for path in (project_path, data_dir, logs_dir, uploads_dir, skills_dir, shared_dir, prompts_dir, personalization_dir):
            path.mkdir(parents=True, exist_ok=True)

        # 数据子目录（工作区级）
        (data_dir / "conversations").mkdir(parents=True, exist_ok=True)
        (data_dir / "backups").mkdir(parents=True, exist_ok=True)

        # 用户级 personalization 主文件（共享）
        ensure_personalization_config(personalization_dir)

        # 为 prompts/personalization 创建便捷访问（保持向后兼容：data_dir 下可作为符号链接）
        for name, target in (("prompts", prompts_dir), ("personalization", personalization_dir)):
            link = data_dir / name
            if not link.exists():
                try:
                    link.symlink_to(target, target_is_directory=True)
                except Exception:
                    # 某些环境禁用 symlink，则忽略，使用共享目录路径显式传递
                    pass

        # 上传隔离区（按用户/工作区划分）
        from config import UPLOAD_QUARANTINE_SUBDIR
        quarantine_root = Path(UPLOAD_QUARANTINE_SUBDIR).expanduser()
        if not quarantine_root.is_absolute():
            quarantine_root = (self.workspace_root.parent / UPLOAD_QUARANTINE_SUBDIR).resolve()
        quarantine_dir = (quarantine_root / username / ws_id).resolve()
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        return ApiUserWorkspace(
            username=username,
            workspace_id=ws_id,
            root=work_root,
            project_path=project_path,
            data_dir=data_dir,
            logs_dir=logs_dir,
            uploads_dir=uploads_dir,
            quarantine_dir=quarantine_dir,
            shared_dir=shared_dir,
            prompts_dir=prompts_dir,
            personalization_dir=personalization_dir,
        )

    def create_user(self, username: str, note: str = "") -> Tuple[ApiUserRecord, str]:
        """创建新的 API 用户并返回明文 Token。"""
        username = self._normalize_username(username)
        with self._lock:
            if username in self._users:
                raise ValueError(tr("api_user_mgr.user_exists"))
            record, token = self._issue_token_locked(username, note=note)
            self._save_users()
            return record, token

    def issue_token(self, username: str, note: str = "") -> Tuple[ApiUserRecord, str]:
        """为已有用户重新生成 token 并返回明文。"""
        username = self._normalize_username(username)
        with self._lock:
            if username not in self._users:
                raise ValueError(tr("api_user_mgr.user_not_found"))
            record, token = self._issue_token_locked(username, note=note)
            self._save_users()
            return record, token

    def delete_user(self, username: str) -> bool:
        username = self._normalize_username(username)
        with self._lock:
            removed = self._users.pop(username, None) is not None
            self._tokens.pop(username, None)
            self._usage.pop(username, None)
            self._save_users()
            self._save_tokens()
            self._save_usage()
        # 尝试删除对应工作区目录（忽略失败）
        try:
            import shutil
            user_root = (self.workspace_root / username).resolve()
            if user_root.exists():
                shutil.rmtree(user_root, ignore_errors=True)
        except Exception:
            pass
        return removed

    def get_plain_token(self, username: str) -> str:
        username = self._normalize_username(username)
        with self._lock:
            token_entry = self._tokens.get(username) or {}
        if not token_entry:
            raise ValueError(tr("api_user_mgr.token_not_found"))
        # 优先使用加密存储；若无密钥或解密失败，回退到明文字段（本地后台安全场景可接受）
        enc = token_entry.get("token_enc")
        if enc:
            fernet = self._fernet()
            if fernet:
                try:
                    return fernet.decrypt(enc.encode("utf-8")).decode("utf-8")
                except InvalidToken as exc:  # type: ignore
                    # fall through to plaintext
                    pass
            else:
                # 没有密钥，继续尝试明文
                pass
        plain = token_entry.get("token_plain")
        if plain:
            return plain
        raise RuntimeError(tr("api_user_mgr.missing_token_secret"))

    def bump_usage(self, username: str, endpoint: Optional[str] = None):
        """记录 API 请求次数与最近时间，用于后台监控。"""
        username = self._normalize_username(username)
        now_iso = datetime.utcnow().isoformat() + "Z"
        with self._lock:
            entry = self._usage.setdefault(username, {"total": 0, "endpoints": {}, "last_request_at": None})
            entry["total"] = int(entry.get("total", 0)) + 1
            if endpoint:
                endpoint_key = endpoint.split("?")[0]
                endpoints = entry.setdefault("endpoints", {})
                endpoints[endpoint_key] = int(endpoints.get(endpoint_key, 0)) + 1
            entry["last_request_at"] = now_iso
            self._save_usage()

    def get_usage(self, username: str) -> Dict[str, Any]:
        username = self._normalize_username(username)
        with self._lock:
            return dict(self._usage.get(username) or {})

    def list_usage(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {u: dict(meta) for u, meta in self._usage.items()}

    def list_workspaces(self, username: str) -> Dict[str, Dict]:
        """列出用户的所有工作区信息。"""
        username = username.strip().lower()
        user_root = (self.workspace_root / username / "workspaces").resolve()
        if not user_root.exists():
            return {}
        result = {}
        for p in sorted(user_root.iterdir()):
            if not p.is_dir():
                continue
            ws_id = p.name
            data_dir = p / "data"
            project_path = p / "project"
            result[ws_id] = {
                "workspace_id": ws_id,
                # 不暴露宿主机绝对路径，只返回相对工作区的信息
                "project_path": "project",
                "data_dir": "data",
                "has_conversations": (data_dir / "conversations").exists(),
            }
        return result

    def delete_workspace(self, username: str, workspace_id: str) -> bool:
        """删除指定工作区（仅工作区目录，不删除共享 prompts/personalization）。"""
        username = username.strip().lower()
        ws_id = (workspace_id or "").strip()
        if not ws_id:
            return False
        # 安全：同 ensure_workspace 的严格白名单校验，防路径穿越删除任意目录
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", ws_id) or ".." in ws_id:
            return False
        work_root = (self.workspace_root / username / "workspaces" / ws_id).resolve()
        # 二次防护：解析后的落点必须严格位于该用户的 workspaces 目录内
        expected_parent = (self.workspace_root / username / "workspaces").resolve()
        if work_root.parent != expected_parent:
            return False
        if not work_root.exists():
            return False
        import shutil
        shutil.rmtree(work_root, ignore_errors=True)
        return True

    # ----------------------- internal helpers -----------------------
    def _normalize_username(self, username: str) -> str:
        candidate = (username or "").strip().lower()
        if not candidate:
            raise ValueError(tr("api_user_mgr.username_empty"))
        return candidate

    def _sha256(self, token: str) -> str:
        return hashlib.sha256((token or "").encode("utf-8")).hexdigest()

    def _load_users(self):
        """加载用户列表，读取 token_sha256；不支持明文存储。"""
        if not self.users_file.exists():
            self._save_users()
            return
        try:
            raw = json.loads(self.users_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(tr("api_user_mgr.users_file_parse_failed", file_path=self.users_file, error=exc))

        users = raw.get("users", {}) if isinstance(raw, dict) else {}
        for username, payload in users.items():
            if not isinstance(payload, dict):
                continue
            token_sha = (payload.get("token_sha256") or "").strip()
            if not token_sha:
                continue
            record = ApiUserRecord(
                username=username.strip().lower(),
                token_sha256=token_sha,
                created_at=payload.get("created_at") or "",
                note=payload.get("note") or "",
            )
            self._users[record.username] = record

    def _save_users(self):
        payload = {
            "users": {
                username: {
                    "token_sha256": record.token_sha256,
                    "created_at": record.created_at or datetime.utcnow().isoformat(),
                    "note": record.note,
                }
                for username, record in self._users.items()
            }
        }
        self.users_file.parent.mkdir(parents=True, exist_ok=True)
        self.users_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # -------- Token 存储（加密回显） --------
    def _fernet(self):
        secret = (API_TOKEN_SECRET or "").strip()
        if not secret or not Fernet:
            return None
        key = hashlib.sha256(secret.encode("utf-8")).digest()
        fkey = base64.urlsafe_b64encode(key)
        return Fernet(fkey)

    def _load_tokens(self):
        if not self.tokens_file.exists():
            self._save_tokens()
            return
        try:
            raw = json.loads(self.tokens_file.read_text(encoding="utf-8"))
            tokens = raw.get("tokens", {}) if isinstance(raw, dict) else {}
            self._tokens = tokens
        except Exception:
            self._tokens = {}

    def _save_tokens(self):
        payload = {"tokens": self._tokens}
        self.tokens_file.parent.mkdir(parents=True, exist_ok=True)
        self.tokens_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _store_token(self, username: str, token: str, note: str = ""):
        fernet = self._fernet()
        enc = fernet.encrypt(token.encode("utf-8")).decode("utf-8") if fernet else ""
        self._tokens[username] = {
            "token_enc": enc,
            "token_plain": token,  # 便于在缺少密钥时回退，受本地文件权限保护
            "note": note,
            "created_at": datetime.utcnow().isoformat(),
        }
        self._save_tokens()

    def _issue_token_locked(self, username: str, note: str = "") -> Tuple[ApiUserRecord, str]:
        token = secrets.token_urlsafe(32)
        token_sha = self._sha256(token)
        record = self._users.get(username)
        if not record:
            record = ApiUserRecord(
                username=username,
                token_sha256=token_sha,
                created_at=datetime.utcnow().isoformat(),
                note=note or "",
            )
        record.token_sha256 = token_sha
        record.note = note or record.note
        record.created_at = record.created_at or datetime.utcnow().isoformat()
        self._users[username] = record
        self._store_token(username, token, note=note)
        return record, token

    # -------- API 请求用量 --------
    def _load_usage(self):
        if not self.usage_file.exists():
            self._save_usage()
            return
        try:
            raw = json.loads(self.usage_file.read_text(encoding="utf-8"))
            usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
            self._usage = usage
        except Exception:
            self._usage = {}

    def _save_usage(self):
        payload = {"usage": self._usage}
        self.usage_file.parent.mkdir(parents=True, exist_ok=True)
        self.usage_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = ["ApiUserManager", "ApiUserRecord", "ApiUserWorkspace"]
