"""User and workspace management utilities for multi-user support."""

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from werkzeug.security import check_password_hash, generate_password_hash

from config import (
    ADMIN_PASSWORD_HASH,
    ADMIN_USERNAME,
    INVITE_CODES_FILE,
    USER_SPACE_DIR,
    USERS_DB_FILE,
    UPLOAD_QUARANTINE_SUBDIR,
    MAIN_MEMORY_FILE,
    TASK_MEMORY_FILE,
    IS_HOST_MODE,
    WEB_DATA_DIR,
    WEB_USER_SPACE_DIR,
)
from modules.personalization_manager import ensure_personalization_config
from modules.personalization_manager import PERSONALIZATION_FILENAME
from modules.i18n import tr


@dataclass
class UserRecord:
    username: str
    email: str
    password_hash: str
    created_at: str
    invite_code: Optional[str] = None
    role: str = "user"
    tutorial_completed: bool = False
    default_workspace_id: str = "default"


@dataclass
class UserWorkspace:
    username: str
    root: Path
    project_path: Path
    data_dir: Path
    logs_dir: Path
    uploads_dir: Path
    quarantine_dir: Path


class UserManager:
    """Handle user registration, authentication and workspace provisioning."""

    USERNAME_REGEX = re.compile(r"^[a-z0-9_\-]{3,32}$")
    RESERVED_WORKSPACE_IDS = {"default", "personal", "shared"}

    def __init__(
        self,
        users_file: str = USERS_DB_FILE,
        invite_codes_file: str = INVITE_CODES_FILE,
        workspace_root: str = USER_SPACE_DIR,
    ):
        self.users_file = Path(users_file)
        self.invite_codes_file = Path(invite_codes_file)
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        # host 模式下同时维护 web 路径引用（双数据源）
        self._web_workspace_root: Optional[Path] = None
        self._web_users_file: Optional[Path] = None
        if IS_HOST_MODE:
            self._web_workspace_root = Path(WEB_USER_SPACE_DIR).expanduser().resolve()
            self._web_users_file = Path(WEB_DATA_DIR) / "users.json"

        self._users: Dict[str, UserRecord] = {}
        self._invites: Dict[str, Dict] = {}
        self._email_map: Dict[str, str] = {}

        self._load_users()
        self._load_invite_codes()
        self._ensure_admin_user()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def register_user(
        self, username: str, email: str, password: str, invite_code: str
    ) -> Tuple[UserRecord, UserWorkspace]:
        username = self._normalize_username(username)
        email = self._normalize_email(email)
        password = password.strip()
        invite_code = (invite_code or "").strip()

        if not password or len(password) < 8:
            raise ValueError(tr("user_mgr.password_min_length"))
        if username in self._users:
            raise ValueError(tr("user_mgr.username_registered"))
        if email in self._email_map:
            raise ValueError(tr("user_mgr.email_registered"))

        invite_entry = self._validate_invite_code(invite_code)
        password_hash = generate_password_hash(password)
        created_at = datetime.utcnow().isoformat()

        record = UserRecord(
            username=username,
            email=email,
            password_hash=password_hash,
            created_at=created_at,
            invite_code=invite_entry["code"],
            tutorial_completed=False,
        )
        self._users[username] = record
        self._index_user(record)
        self._save_users()
        self._consume_invite(invite_entry)

        workspace = self.ensure_user_workspace(username)
        return record, workspace

    def authenticate(self, email: str, password: str) -> Optional[UserRecord]:
        email = (email or "").strip().lower()
        username = self._email_map.get(email)
        if not username:
            return None
        record = self._users.get(username)
        if not record or not record.password_hash:
            return None
        if not check_password_hash(record.password_hash, password or ""):
            return None
        return record

    def get_user(self, username: str) -> Optional[UserRecord]:
        return self._users.get((username or "").strip().lower())

    @staticmethod
    def normalize_workspace_id(workspace_id: str) -> str:
        candidate = (workspace_id or "default").strip()
        if not candidate:
            candidate = "default"
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", candidate):
            raise ValueError(tr("user_mgr.workspace_id_rule"))
        return candidate

    @classmethod
    def validate_new_workspace_id(cls, workspace_id: str) -> str:
        candidate = cls.normalize_workspace_id(workspace_id)
        if candidate in cls.RESERVED_WORKSPACE_IDS or candidate.startswith("."):
            raise ValueError(tr("user_mgr.workspace_id_reserved"))
        return candidate

    def _project_container_root(self, username: str) -> Path:
        """返回 host 模式 projects 容器目录（写操作始终走 host 路径）。"""
        return (self.workspace_root / username / "projects").resolve()

    def _personal_root(self, username: str) -> Path:
        return (self.workspace_root / username / "personal").resolve()

    @staticmethod
    def _looks_like_project_workspace_dir(path: Path) -> bool:
        return path.is_dir() and ((path / "project").exists() or (path / "data").exists())

    def _move_dir_contents(self, source: Path, target: Path) -> None:
        if not source.exists() or not source.is_dir():
            return
        target.mkdir(parents=True, exist_ok=True)
        for child in list(source.iterdir()):
            dest = target / child.name
            if dest.exists():
                continue
            shutil.move(str(child), str(dest))

    def _ensure_shared_file_link(self, link_path: Path, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.parent.mkdir(parents=True, exist_ok=True)
        if link_path.is_symlink():
            try:
                if link_path.resolve() == target_path.resolve():
                    return
            except Exception:
                pass
            try:
                link_path.unlink()
            except Exception:
                return
        if link_path.exists():
            if not link_path.is_file():
                return
            try:
                should_adopt_existing = (
                    not target_path.exists()
                    or (target_path.is_file() and target_path.stat().st_size == 0)
                )
            except Exception:
                should_adopt_existing = not target_path.exists()
            if should_adopt_existing:
                try:
                    shutil.copy2(str(link_path), str(target_path))
                except Exception:
                    return
            try:
                link_path.unlink()
            except Exception:
                return
        if not target_path.exists():
            target_path.touch()
        try:
            link_path.symlink_to(target_path)
        except Exception:
            if not link_path.exists() and target_path.exists():
                # Windows 无管理员权限/未开开发者模式时 symlink 常失败。
                # 优先退回硬链接：同卷内无需特权，且两侧指向同一文件内容，
                # 保持「项目间共享用户状态」的语义，不会像 copy 那样静默分叉。
                try:
                    os.link(target_path, link_path)
                    return
                except Exception:
                    pass
                # 跨卷/文件系统不支持硬链接时最后退回复制（可能分叉，仅兜底）。
                try:
                    shutil.copy2(target_path, link_path)
                except Exception:
                    pass

    def _ensure_shared_user_state_links(self, username: str, data_dir: Path) -> None:
        """项目间共享用户级个性化与记忆，同时保持 data_dir 路径兼容。"""
        personal_dir = self._personal_root(username)
        personalization_dir = personal_dir / "personalization"
        memory_dir = personal_dir / "memory"
        personalization_dir.mkdir(parents=True, exist_ok=True)
        memory_dir.mkdir(parents=True, exist_ok=True)

        self._ensure_shared_file_link(
            data_dir / PERSONALIZATION_FILENAME,
            personalization_dir / PERSONALIZATION_FILENAME,
        )
        ensure_personalization_config(personalization_dir)
        self._ensure_shared_file_link(
            data_dir / Path(MAIN_MEMORY_FILE).name,
            memory_dir / Path(MAIN_MEMORY_FILE).name,
        )
        self._ensure_shared_file_link(
            data_dir / Path(TASK_MEMORY_FILE).name,
            memory_dir / Path(TASK_MEMORY_FILE).name,
        )

    def _migrate_legacy_docker_workspace_layout(self, username: str) -> None:
        """把旧 Docker Web 单项目目录迁移为 projects/default/{project,data,logs}。

        旧布局：
          <user>/project/*
          <user>/data/*
          <user>/logs/*

        新布局：
          <user>/projects/default/project/*
          <user>/projects/default/data/*
          <user>/projects/default/logs/*
          <user>/personal/{personalization,memory,agentskills}
        """
        root = (self.workspace_root / username).resolve()
        projects_root = self._project_container_root(username)
        personal_root = self._personal_root(username)
        legacy_project_root = root / "project"
        default_root = projects_root / "default"
        default_project = default_root / "project"
        default_data = default_root / "data"
        default_logs = default_root / "logs"
        marker = default_root / ".migrated_from_legacy"

        # 兼容上一轮临时结构 users/<user>/project/<id> 与 users/<user>/project/shared。
        if legacy_project_root.exists() and legacy_project_root.is_dir():
            projects_root.mkdir(parents=True, exist_ok=True)

            legacy_shared = legacy_project_root / "shared"
            if legacy_shared.exists() and legacy_shared.is_dir():
                for sub in ("personalization", "memory", "agentskills"):
                    self._move_dir_contents(legacy_shared / sub, personal_root / sub)
                try:
                    shutil.rmtree(legacy_shared, ignore_errors=True)
                except Exception:
                    pass

            for item in list(legacy_project_root.iterdir()):
                if item.name.startswith("."):
                    continue
                if item.name in self.RESERVED_WORKSPACE_IDS - {"default"}:
                    continue
                if self._looks_like_project_workspace_dir(item):
                    dest = projects_root / item.name
                    if not dest.exists():
                        shutil.move(str(item), str(dest))

            # 迁移最老布局 users/<user>/project/* 下的实际文件到 default/project。
            if legacy_project_root.exists() and not marker.exists():
                legacy_children = [
                    child
                    for child in list(legacy_project_root.iterdir())
                    if (
                        not child.name.startswith(".")
                        and not (child.is_dir() and child.name in (self.RESERVED_WORKSPACE_IDS - {"project"}))
                        and not self._looks_like_project_workspace_dir(child)
                    )
                ]
                if legacy_children:
                    default_project.mkdir(parents=True, exist_ok=True)
                    for child in legacy_children:
                        dest = default_project / child.name
                        if not dest.exists():
                            shutil.move(str(child), str(dest))
            try:
                legacy_project_root.rmdir()
            except Exception:
                pass

        # 迁移旧 data/logs。
        legacy_data = root / "data"
        legacy_logs = root / "logs"
        if legacy_data.exists() and legacy_data.is_dir() and legacy_data.resolve() != default_data.resolve():
            if not default_data.exists():
                default_data.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy_data), str(default_data))
            else:
                self._move_dir_contents(legacy_data, default_data)
                try:
                    legacy_data.rmdir()
                except Exception:
                    pass
        if legacy_logs.exists() and legacy_logs.is_dir() and legacy_logs.resolve() != default_logs.resolve():
            if not default_logs.exists():
                default_logs.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy_logs), str(default_logs))
            else:
                self._move_dir_contents(legacy_logs, default_logs)
                try:
                    legacy_logs.rmdir()
                except Exception:
                    pass
        legacy_agentskills = root / "agentskills"
        if legacy_agentskills.exists() and legacy_agentskills.is_dir():
            self._move_dir_contents(legacy_agentskills, personal_root / "agentskills")
            try:
                legacy_agentskills.rmdir()
            except Exception:
                pass
        try:
            default_root.mkdir(parents=True, exist_ok=True)
            marker.write_text("ok", encoding="utf-8")
        except Exception:
            pass

    def ensure_user_workspace(self, username: str, workspace_id: str = "default") -> UserWorkspace:
        username = self._normalize_username(username)
        workspace_id = self.normalize_workspace_id(workspace_id)

        # host 模式：检查 web 路径是否已有该工作区（保持旧数据可访问）
        if IS_HOST_MODE and self._web_workspace_root:
            web_ws_root = (self._web_workspace_root / username / "projects" / workspace_id).resolve()
            if web_ws_root.exists():
                return self._build_workspace(username, workspace_id, web_ws_root)

        root = (self.workspace_root / username).resolve()
        self._migrate_legacy_docker_workspace_layout(username)
        workspace_root = self._project_container_root(username) / workspace_id
        return self._build_workspace(username, workspace_id, workspace_root)

    def _build_workspace(self, username: str, workspace_id: str, workspace_root: Path) -> UserWorkspace:
        """根据 workspace_root 构建 UserWorkspace 并创建必要目录。"""
        project_path = workspace_root / "project"
        data_dir = workspace_root / "data"
        logs_dir = workspace_root / "logs"
        uploads_dir = project_path / ".astrion" / "user_upload"
        skills_dir = project_path / ".astrion" / "skills"

        for path in [project_path, data_dir, logs_dir, uploads_dir, skills_dir]:
            path.mkdir(parents=True, exist_ok=True)

        # 初始化数据子目录
        (data_dir / "conversations").mkdir(parents=True, exist_ok=True)
        (data_dir / "backups").mkdir(parents=True, exist_ok=True)
        self._ensure_shared_user_state_links(username, data_dir)

        quarantine_root = Path(UPLOAD_QUARANTINE_SUBDIR).expanduser()
        if not quarantine_root.is_absolute():
            quarantine_root = (self.workspace_root.parent / UPLOAD_QUARANTINE_SUBDIR).resolve()
        quarantine_dir = (quarantine_root / username / workspace_id).resolve()
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        workspace = UserWorkspace(
            username=username,
            root=workspace_root,
            project_path=project_path,
            data_dir=data_dir,
            logs_dir=logs_dir,
            uploads_dir=uploads_dir,
            quarantine_dir=quarantine_dir,
        )
        workspace.workspace_id = workspace_id
        return workspace

    def _get_user_default_workspace_id(self, username: str) -> str:
        """返回用户设置的默认项目 ID，如未设置或无效则返回 default。"""
        record = self._users.get(self._normalize_username(username))
        default_id = str(getattr(record, "default_workspace_id", None) or "default").strip()
        if not default_id:
            return "default"
        return default_id

    def list_user_workspaces(self, username: str) -> Dict[str, Dict]:
        username = self._normalize_username(username)
        self._migrate_legacy_docker_workspace_layout(username)
        result: Dict[str, Dict] = {}

        # 收集主路径的工作区
        self._collect_workspaces(result, self._project_container_root(username))

        # host 模式：额外收集 web 路径的工作区
        if IS_HOST_MODE and self._web_workspace_root:
            web_root = (self._web_workspace_root / username / "projects").resolve()
            if web_root.exists():
                self._collect_workspaces(result, web_root)

        default_workspace_id = self._get_user_default_workspace_id(username)

        # 确保 default 始终存在
        if "default" not in result:
            result["default"] = {
                "workspace_id": "default",
                "label": "默认项目",
                "path": "",
                "is_default": default_workspace_id == "default",
                "has_conversations": False,
            }

        # 同步 is_default 标记
        for ws_id in result:
            result[ws_id]["is_default"] = ws_id == default_workspace_id
        return result

    def _collect_workspaces(self, result: Dict[str, Dict], projects_root: Path) -> None:
        """扫描 projects_root 下的工作区，合并到 result（跳过已存在的）。"""
        default_project = projects_root / "default" / "project"
        default_data = projects_root / "default" / "data"
        if "default" not in result:
            if default_project.exists() or default_data.exists():
                result["default"] = {
                    "workspace_id": "default",
                    "label": "默认项目",
                    "path": "",
                    "is_default": True,
                    "has_conversations": (default_data / "conversations").exists(),
                }

        if not projects_root.exists():
            return
        for item in sorted(projects_root.iterdir(), key=lambda p: p.name.lower()):
            if not item.is_dir():
                continue
            ws_id = item.name
            if ws_id in self.RESERVED_WORKSPACE_IDS or ws_id.startswith("."):
                continue
            if ws_id in result:
                continue
            data_dir = item / "data"
            result[ws_id] = {
                "workspace_id": ws_id,
                "label": self._load_workspace_label(item, ws_id),
                "path": "",
                "is_default": False,
                "has_conversations": (data_dir / "conversations").exists(),
            }

    def generate_workspace_id(self, username: str, label: str = "") -> str:
        username = self._normalize_username(username)
        raw = (label or "project").strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
        if not slug or slug in self.RESERVED_WORKSPACE_IDS or slug.startswith("."):
            slug = "project"
        slug = slug[:32].strip("-") or "project"
        existing = set(self.list_user_workspaces(username).keys())
        candidate = slug
        idx = 2
        while candidate in existing or candidate in self.RESERVED_WORKSPACE_IDS or candidate.startswith("."):
            suffix = f"-{idx}"
            candidate = f"{slug[: max(1, 40 - len(suffix))]}{suffix}"
            idx += 1
        return candidate

    def create_user_workspace(self, username: str, workspace_id: str = "", label: str = "") -> UserWorkspace:
        ws_id = (
            self.validate_new_workspace_id(workspace_id)
            if workspace_id
            else self.generate_workspace_id(username, label)
        )
        workspace = self.ensure_user_workspace(username, ws_id)
        label_text = (label or "").strip()
        if label_text:
            meta_path = Path(workspace.root) / "project.json"
            try:
                meta_path.write_text(
                    json.dumps({"workspace_id": ws_id, "label": label_text}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
        return workspace

    def rename_user_workspace(self, username: str, workspace_id: str, label: str) -> Dict:
        username = self._normalize_username(username)
        ws_id = self.normalize_workspace_id(workspace_id)
        label_text = (label or "").strip()
        if not label_text:
            raise ValueError(tr("user_mgr.workspace_name_empty"))
        known = self.list_user_workspaces(username)
        if ws_id not in known:
            raise ValueError(tr("user_mgr.workspace_not_found"))
        workspace = self.ensure_user_workspace(username, ws_id)
        meta_path = Path(workspace.root) / "project.json"
        meta_path.write_text(
            json.dumps({"workspace_id": ws_id, "label": label_text}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "workspace_id": ws_id,
            "label": label_text,
            "path": "",
            "is_default": ws_id == "default",
            "has_conversations": (Path(workspace.data_dir) / "conversations").exists(),
        }

    def delete_user_workspace(self, username: str, workspace_id: str) -> None:
        username = self._normalize_username(username)
        ws_id = self.validate_new_workspace_id(workspace_id)
        projects_root = self._project_container_root(username)
        target = (projects_root / ws_id).resolve()
        try:
            target.relative_to(projects_root.resolve())
        except ValueError as exc:
            raise ValueError(tr("user_mgr.workspace_path_invalid")) from exc
        if not target.exists() or not target.is_dir():
            raise ValueError(tr("user_mgr.workspace_not_found"))
        shutil.rmtree(target)

    def set_default_user_workspace(self, username: str, workspace_id: str) -> str:
        """设置指定项目为用户默认项目，返回生效的默认项目 ID。"""
        username = self._normalize_username(username)
        ws_id = self.normalize_workspace_id(workspace_id)
        known = self.list_user_workspaces(username)
        if ws_id not in known:
            raise ValueError(tr("user_mgr.workspace_not_found"))
        record = self._users.get(username)
        if not record:
            raise ValueError(tr("user_mgr.user_not_found"))
        record.default_workspace_id = ws_id
        self._save_users()
        return ws_id

    @staticmethod
    def _load_workspace_label(workspace_root: Path, fallback: str) -> str:
        meta_path = workspace_root / "project.json"
        if meta_path.exists():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                label = str(data.get("label") or "").strip()
                if label:
                    return label
            except Exception:
                pass
        return fallback

    def list_invite_codes(self):
        return list(self._invites.values())

    def upsert_invite_code(self, code: str, remaining: Optional[int]) -> Dict:
        """新增或更新邀请码。remaining=None 表示不限次数。"""
        invite_code = (code or "").strip()
        if not invite_code:
            raise ValueError(tr("user_mgr.invite_code_empty"))
        if len(invite_code) > 64:
            raise ValueError(tr("user_mgr.invite_code_too_long"))
        if remaining is not None:
            try:
                remaining = int(remaining)
            except (TypeError, ValueError):
                raise ValueError(tr("user_mgr.remaining_must_be_integer_or_null"))
            if remaining < 0:
                raise ValueError(tr("user_mgr.remaining_not_negative"))
        entry = {
            "code": invite_code,
            "remaining": remaining,
        }
        self._invites[invite_code] = entry
        self._save_invite_codes()
        return dict(entry)

    def delete_invite_code(self, code: str) -> bool:
        invite_code = (code or "").strip()
        if not invite_code:
            raise ValueError(tr("user_mgr.invite_code_empty"))
        existed = invite_code in self._invites
        if existed:
            self._invites.pop(invite_code, None)
            self._save_invite_codes()
        return existed

    def list_users(self) -> Dict[str, UserRecord]:
        """返回当前注册用户的浅拷贝字典，键为用户名。"""
        return dict(self._users)

    def set_tutorial_completed(self, username: str, completed: bool = True) -> Optional[UserRecord]:
        key = (username or "").strip().lower()
        record = self._users.get(key)
        if not record:
            return None
        record.tutorial_completed = bool(completed)
        self._save_users()
        return record

    def reset_password(self, username: str, new_password: str) -> UserRecord:
        """重置用户密码。管理员操作。"""
        key = (username or "").strip().lower()
        password = (new_password or "").strip()
        if not password or len(password) < 8:
            raise ValueError(tr("user_mgr.password_min_length"))
        record = self._users.get(key)
        if not record:
            raise ValueError(tr("user_mgr.user_not_found_dot"))
        record.password_hash = generate_password_hash(password)
        self._save_users()
        return record

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _normalize_username(self, username: str) -> str:
        candidate = (username or "").strip().lower()
        if not candidate or not self.USERNAME_REGEX.match(candidate):
            raise ValueError(tr("user_mgr.username_rule"))
        return candidate

    def _normalize_email(self, email: str) -> str:
        email = (email or "").strip().lower()
        if "@" not in email or len(email) < 6:
            raise ValueError(tr("user_mgr.email_invalid"))
        return email

    def _index_user(self, record: UserRecord):
        email = (record.email or '').strip().lower()
        if email:
            self._email_map[email] = record.username

    def _load_users(self):
        if not self.users_file.exists():
            self._save_users()
            # host 模式：即使主文件不存在也尝试加载 web 用户
            self._load_users_from(self.users_file)
            if IS_HOST_MODE and self._web_users_file and self._web_users_file.exists():
                self._load_users_from(self._web_users_file, skip_existing=True)
            return

        self._load_users_from(self.users_file)
        # host 模式：额外合并 web 用户（不覆盖已有）
        if IS_HOST_MODE and self._web_users_file and self._web_users_file.exists():
            self._load_users_from(self._web_users_file, skip_existing=True)

    def _load_users_from(self, file_path: Path, skip_existing: bool = False):
        """从指定文件加载用户，skip_existing 为 True 时跳过已有用户。"""
        if not file_path.exists():
            return
        try:
            migrated = False
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    raw_users = data.get("users", {})
                elif isinstance(data, list):
                    raw_users = {item.get("username"): item for item in data if isinstance(item, dict) and item.get("username")}
                else:
                    raw_users = {}
                for username, payload in raw_users.items():
                    if skip_existing and username in self._users:
                        continue
                    record = UserRecord(
                        username=username,
                        email=payload.get("email", ""),
                        password_hash=payload.get("password_hash", ""),
                        created_at=payload.get("created_at", ""),
                        invite_code=payload.get("invite_code"),
                        role=payload.get("role", "user"),
                        tutorial_completed=bool(payload.get("tutorial_completed", False)),
                        default_workspace_id=str(payload.get("default_workspace_id") or "default").strip() or "default",
                    )
                    if "tutorial_completed" not in payload:
                        migrated = True
                    if "default_workspace_id" not in payload:
                        migrated = True
                    self._users[username] = record
                    self._index_user(record)
            if migrated:
                self._save_users()
        except json.JSONDecodeError:
            raise RuntimeError(tr("user_mgr.users_file_parse_failed", file_path=file_path))

    def _save_users(self):
        payload = {
            "users": {
                username: {
                    "email": record.email,
                    "password_hash": record.password_hash,
                    "created_at": record.created_at,
                    "invite_code": record.invite_code,
                    "role": record.role,
                    "tutorial_completed": bool(record.tutorial_completed),
                    "default_workspace_id": str(record.default_workspace_id or "default").strip() or "default",
                }
                for username, record in self._users.items()
            }
        }
        self.users_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.users_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _load_invite_codes(self):
        if not self.invite_codes_file.exists():
            self._save_invite_codes({})
            return

        try:
            with open(self.invite_codes_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    codes = data.get("codes", [])
                elif isinstance(data, list):
                    codes = data
                else:
                    codes = []
                self._invites = {item["code"]: item for item in codes if isinstance(item, dict) and "code" in item}
        except json.JSONDecodeError:
            raise RuntimeError(tr("user_mgr.invite_file_parse_failed", file_path=self.invite_codes_file))

    def _save_invite_codes(self, overrides: Optional[Dict[str, Dict]] = None):
        codes = overrides or self._invites
        payload = {"codes": list(codes.values())}
        self.invite_codes_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.invite_codes_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _validate_invite_code(self, code: str) -> Dict:
        if not code:
            raise ValueError(tr("user_mgr.invite_code_empty"))
        entry = self._invites.get(code)
        if not entry:
            raise ValueError(tr("user_mgr.invite_code_invalid"))

        remaining = entry.get("remaining")
        if remaining is not None and remaining <= 0:
            raise ValueError(tr("user_mgr.invite_code_used"))
        return entry

    def _consume_invite(self, entry: Dict):
        if entry.get("remaining") is None:
            return
        entry["remaining"] = max(0, entry["remaining"] - 1)
        self._save_invite_codes()

    def _ensure_admin_user(self):
        admin_name = (ADMIN_USERNAME or "").strip().lower()
        if not admin_name or not ADMIN_PASSWORD_HASH:
            return

        if admin_name in self._users:
            return

        record = UserRecord(
            username=admin_name,
            email=f"{admin_name}@local",
            password_hash=ADMIN_PASSWORD_HASH,
            created_at=datetime.utcnow().isoformat(),
            invite_code=None,
            role="admin",
            tutorial_completed=False,
        )
        self._users[admin_name] = record
        self._index_user(record)
        self._save_users()
        self.ensure_user_workspace(admin_name)
