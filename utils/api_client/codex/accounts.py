"""Codex 多账号注册表（2026-10-29）。

设计要点（用户要求，勿改语义）：

- **凭证隔离**：每个账号一份独立凭证文件（结构同 CLI auth.json，0600），
  独立 refresh token 族；刷新只回写自己的文件，绝不互相覆盖。
  - 主账号（legacy）沿用 ``codex_auth.json``，连接 id 固定
    ``openai-codex``——旧模型 key ``openai-codex/<slug>`` 因此继续有效；
  - 新增账号凭证放 ``codex_accounts/<connection_id>.json``。
- **稳定账号哈希**：显示名 ``OpenAI（ChatGPT 订阅）-<短哈希>``；哈希源是
  ChatGPT 账号身份（id_token 的 chatgpt_account_id / sub / email），
  **绝不哈希 token**（token 会轮换）。6 位前缀碰撞时只对新账号加长，
  已有账号显示哈希保持不变（稳定），保证全局唯一。
- **重复登录同账号 = 更新原凭证**：登录完成后按身份哈希匹配已有账号，
  命中即原地更新该账号凭证文件（不新建账号、不改变其连接 id / 显示哈希）。
- **账号索引** ``codex_accounts.json`` 只存连接 id / 身份哈希 / 显示哈希 /
  时间，不存任何 token；token 只在各自凭证文件里。
- **删除 = 删凭证文件 + 索引条目**（真登出）；不做墓碑、不复用连接 id。
- **主账号规则**（CrossPet 等旧单账号消费者）：legacy 账号优先，否则最早
  添加的账号；被删除后自然顺延到下一个（每次按索引重算，不依赖旧文件是否
  仍存在作为优先级依据）。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.api_client.codex.auth import CodexAuthManager, _jwt_payload
from utils.api_client.codex.settings import (
    CODEX_ACCOUNT_DISPLAY_BASE,
    CODEX_ACCOUNTS_DIR,
    CODEX_ACCOUNTS_INDEX_PATH,
    CODEX_AUTH_PATH,
    LEGACY_CODEX_ACCOUNT_ID,
)

_INDEX_VERSION = 1
_NEW_ACCOUNT_PREFIX = "openai-codex-"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _file_mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
    except OSError:
        return _now_iso()


def codex_account_identity(id_token: str, account_id: Optional[str] = None) -> str:
    """提取稳定账号身份（绝不使用 token 本体）。

    优先级：id_token 的 chatgpt_account_id（与凭证文件 tokens.account_id 同源）
    → sub → email。全部缺失返回空串（无法去重，只能作为独立账号）。
    """
    claims = _jwt_payload(str(id_token or ""))
    auth_claim = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        chatgpt_account_id = auth_claim.get("chatgpt_account_id")
        if chatgpt_account_id:
            return f"account:{chatgpt_account_id}"
    if account_id:
        return f"account:{account_id}"
    sub = claims.get("sub")
    if sub:
        return f"sub:{sub}"
    email = claims.get("email")
    if email:
        return f"email:{str(email).strip().lower()}"
    return ""


def identity_hash_from_material(material: str) -> str:
    text = str(material or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_private_json(path: Path, data: Dict[str, Any]) -> None:
    """原子写 JSON（0600）：临时文件 + fsync + os.replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp-astrion")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _display_hash(identity_hash: str) -> str:
    """无身份信息时的兜底显示哈希（不来自 token）。"""
    return hashlib.sha256(f"unidentified:{identity_hash}".encode("utf-8")).hexdigest()


class CodexAccountStore:
    """Codex 账号注册表 + 每账号 CodexAuthManager 工厂（进程级单例）。"""

    def __init__(
        self,
        legacy_auth_path: Optional[str] = None,
        accounts_dir: Optional[str] = None,
        index_path: Optional[str] = None,
    ) -> None:
        self.legacy_auth_path = Path(legacy_auth_path or CODEX_AUTH_PATH)
        self.accounts_dir = Path(accounts_dir or CODEX_ACCOUNTS_DIR)
        self.index_path = Path(index_path or CODEX_ACCOUNTS_INDEX_PATH)
        self._lock = threading.RLock()
        self._managers: Dict[str, CodexAuthManager] = {}
        self._legacy_manager: Optional[CodexAuthManager] = None

    # ------------------------------------------------------------ 索引读写

    def _load_index_locked(self) -> Dict[str, Any]:
        data = _read_json(self.index_path)
        accounts = data.get("accounts")
        if not isinstance(accounts, dict):
            accounts = {}
        index: Dict[str, Any] = {"version": _INDEX_VERSION, "accounts": accounts}
        changed = False
        # 2026-10-29 修正：正常读取不得把“凭证文件暂不可用”当成登出。
        # 索引条目就是已登记身份：文件暂缺（同步盘未就绪/临时删除/恢复中）时
        # 保留 id/hash/时间，由 _public_record 报 credential_missing；只有显式
        # logout 才删登记（不做删除墓碑，也不在读取时偷偷重分配新 id）。
        # 迁移/自愈：legacy 凭证文件 + 目录里已有但未登记的文件（幂等）
        if self._migrate_legacy_locked(index):
            changed = True
        if self._discover_account_files_locked(index):
            changed = True
        if changed:
            # 只读数据目录（如受限沙箱）里不阻断查询：注册表变更仅保留在内存，
            # 真正写凭证的操作会单独报错。
            self._save_index_locked(index, best_effort=True)
        return index

    def _save_index_locked(self, index: Dict[str, Any], best_effort: bool = False) -> None:
        try:
            _write_private_json(
                self.index_path,
                {"version": _INDEX_VERSION, "accounts": index.get("accounts") or {}},
            )
        except OSError:
            if not best_effort:
                raise

    def _migrate_legacy_locked(self, index: Dict[str, Any]) -> bool:
        """把旧 ``codex_auth.json`` 登记为主账号（一次性、幂等）。"""
        accounts = index["accounts"]
        if LEGACY_CODEX_ACCOUNT_ID in accounts:
            return False
        if not self.legacy_auth_path.exists():
            return False
        data = _read_json(self.legacy_auth_path)
        tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
        if not tokens.get("access_token") and not tokens.get("refresh_token"):
            return False
        material = codex_account_identity(str(tokens.get("id_token") or ""), tokens.get("account_id"))
        identity_hash = identity_hash_from_material(material)
        accounts[LEGACY_CODEX_ACCOUNT_ID] = {
            "identity_hash": identity_hash,
            "display_hash": self._assign_display_hash_locked(
                index, identity_hash, LEGACY_CODEX_ACCOUNT_ID
            ),
            "created_at": _file_mtime_iso(self.legacy_auth_path),
            "last_login_at": data.get("last_refresh") or _file_mtime_iso(self.legacy_auth_path),
            "legacy": True,
        }
        return True

    def _discover_account_files_locked(self, index: Dict[str, Any]) -> bool:
        """扫描凭证目录，登记未入索引的文件（中断写入自愈；幂等）。"""
        accounts = index["accounts"]
        changed = False
        if not self.accounts_dir.exists():
            return False
        for path in sorted(self.accounts_dir.glob("*.json")):
            account_id = path.stem
            if account_id in accounts:
                continue
            data = _read_json(path)
            tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
            if not tokens.get("access_token") and not tokens.get("refresh_token"):
                continue
            material = codex_account_identity(
                str(tokens.get("id_token") or ""), tokens.get("account_id")
            )
            identity_hash = identity_hash_from_material(material)
            accounts[account_id] = {
                "identity_hash": identity_hash,
                "display_hash": self._assign_display_hash_locked(index, identity_hash, account_id),
                "created_at": _file_mtime_iso(path),
                "last_login_at": data.get("last_refresh") or _file_mtime_iso(path),
                "legacy": False,
            }
            changed = True
        return changed

    def _assign_display_hash_locked(
        self, index: Dict[str, Any], identity_hash: str, account_id: str
    ) -> str:
        """分配账号短哈希：至少 6 位；只加长新账号，已有账号保持稳定。"""
        used = {
            str(rec.get("display_hash") or "")
            for aid, rec in (index.get("accounts") or {}).items()
            if aid != account_id and rec.get("display_hash")
        }
        source = identity_hash or hashlib.sha256(f"account:{account_id}".encode()).hexdigest()
        for length in (6, 8, 10, 12, 16, 24, 32, len(source)):
            candidate = source[:length]
            if candidate not in used:
                return candidate
        # 理论上只剩同一身份（应当被去重掉），返回全长保证唯一性
        return source

    # ------------------------------------------------------------ 凭证路径

    def _credential_path(self, account_id: str) -> Path:
        if account_id == LEGACY_CODEX_ACCOUNT_ID:
            return self.legacy_auth_path
        return self.accounts_dir / f"{account_id}.json"

    def _legacy_manager_locked(self) -> CodexAuthManager:
        if self._legacy_manager is None:
            self._legacy_manager = CodexAuthManager(str(self.legacy_auth_path))
        return self._legacy_manager

    # ------------------------------------------------------------ 查询

    def list_accounts(self) -> List[Dict[str, Any]]:
        with self._lock:
            index = self._load_index_locked()
            records = [
                self._public_record(account_id, rec)
                for account_id, rec in index["accounts"].items()
            ]
        # 顺序：legacy 主账号在前，其余按创建时间（主账号规则同源）
        records.sort(key=lambda r: (0 if r["is_legacy"] else 1, r.get("created_at") or "", r["id"]))
        return records

    def _public_record(self, account_id: str, rec: Dict[str, Any]) -> Dict[str, Any]:
        display_hash = str(rec.get("display_hash") or "")
        path = self._credential_path(account_id)
        file_present = path.exists()
        connected = False
        if file_present:
            try:
                manager = self.get_auth_manager(account_id)
                connected = bool(manager.status().get("connected"))
            except Exception:
                connected = file_present
        # 凭证暂缺 ≠ 登出：身份仍登记、仍在选择器可诊断，只是不可用于请求。
        state = "ready" if connected else ("credential_missing" if not file_present else "disconnected")
        return {
            "id": account_id,
            "hash": display_hash,
            "display_name": self.display_name(display_hash),
            "created_at": rec.get("created_at"),
            "last_login_at": rec.get("last_login_at"),
            "is_legacy": bool(rec.get("legacy")),
            "connected": connected,
            "state": state,
            "credential_missing": not file_present,
        }

    @staticmethod
    def display_name(display_hash: str) -> str:
        suffix = str(display_hash or "").strip()
        return f"{CODEX_ACCOUNT_DISPLAY_BASE}-{suffix}" if suffix else CODEX_ACCOUNT_DISPLAY_BASE

    def get_account(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        """取账号公开记录；account_id 为空时用主账号。不存在抛 KeyError。"""
        with self._lock:
            resolved = self._resolve_account_id_locked(account_id)
            index = self._load_index_locked()
            rec = index["accounts"].get(resolved)
            if rec is None:
                raise KeyError(resolved)
            return self._public_record(resolved, rec)

    def resolve_account_id(self, account_id: Optional[str] = None) -> str:
        with self._lock:
            return self._resolve_account_id_locked(account_id)

    def _resolve_account_id_locked(self, account_id: Optional[str]) -> str:
        account_id = str(account_id or "").strip()
        if account_id:
            index = self._load_index_locked()
            if account_id not in index["accounts"]:
                raise KeyError(account_id)
            return account_id
        primary = self.primary_account_id_locked()
        if not primary:
            raise KeyError("no_codex_account")
        return primary

    def primary_account_id(self) -> Optional[str]:
        with self._lock:
            return self.primary_account_id_locked()

    def primary_account_id_locked(self) -> Optional[str]:
        """主账号：legacy 优先，否则最早添加的账号（不依赖旧文件存在性做优先级）。"""
        index = self._load_index_locked()
        accounts = index["accounts"]
        if LEGACY_CODEX_ACCOUNT_ID in accounts:
            return LEGACY_CODEX_ACCOUNT_ID
        if not accounts:
            return None
        ordered = sorted(
            accounts.items(),
            key=lambda item: (str(item[1].get("created_at") or ""), item[0]),
        )
        return ordered[0][0]

    def get_auth_manager(self, account_id: Optional[str] = None) -> CodexAuthManager:
        with self._lock:
            resolved = self._resolve_account_id_locked(account_id)
            if resolved == LEGACY_CODEX_ACCOUNT_ID:
                return self._legacy_manager_locked()
            manager = self._managers.get(resolved)
            if manager is None:
                manager = CodexAuthManager(str(self._credential_path(resolved)))
                self._managers[resolved] = manager
            return manager

    def get_models_cache_path(self, account_id: Optional[str] = None) -> str:
        """每账号模型缓存文件路径：主账号沿用旧全局缓存文件（保留旧数据与配置键）。"""
        resolved = self.resolve_account_id(account_id)
        if resolved == LEGACY_CODEX_ACCOUNT_ID:
            from utils.api_client.codex.settings import CODEX_DATA_PATH

            return CODEX_DATA_PATH
        from utils.api_client.codex.settings import CODEX_MODELS_DIR

        return str(Path(CODEX_MODELS_DIR) / f"{resolved}.json")

    # ------------------------------------------------------------ 登录落盘 / 登出

    def upsert_tokens(
        self,
        *,
        access_token: str,
        refresh_token: str,
        id_token: str = "",
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """登录完成写凭证：同身份更新原账号，否则新建账号（原子、幂等）。"""
        material = codex_account_identity(id_token, account_id)
        identity_hash = identity_hash_from_material(material)
        with self._lock:
            index = self._load_index_locked()
            accounts = index["accounts"]
            target: Optional[str] = None
            if identity_hash:
                for aid, rec in accounts.items():
                    if str(rec.get("identity_hash") or "") == identity_hash:
                        target = aid
                        break
            now = _now_iso()
            if target is None:
                target = self._new_connection_id_locked(accounts)
                accounts[target] = {
                    "identity_hash": identity_hash,
                    "display_hash": self._assign_display_hash_locked(index, identity_hash, target),
                    "created_at": now,
                    "last_login_at": now,
                    "legacy": False,
                }
            else:
                accounts[target]["last_login_at"] = now
                if not accounts[target].get("display_hash"):
                    accounts[target]["display_hash"] = self._assign_display_hash_locked(
                        index, identity_hash, target
                    )
            existing = _read_json(self._credential_path(target))
            payload = {
                "auth_mode": existing.get("auth_mode") or "chatgpt",
                "OPENAI_API_KEY": existing.get("OPENAI_API_KEY"),
                "tokens": {
                    "id_token": id_token,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "account_id": account_id,
                },
                "last_refresh": now,
            }
            _write_private_json(self._credential_path(target), payload)
            self._save_index_locked(index)
            # 强制下次读取感知新文件（CodexAuthManager 自身也有文件签名检测）
            manager = self._managers.get(target)
            if manager is not None:
                manager._cached = None
                manager._cached_stat = None
            return self._public_record(target, accounts[target])

    def _new_connection_id_locked(self, accounts: Dict[str, Any]) -> str:
        for _ in range(32):
            candidate = f"{_NEW_ACCOUNT_PREFIX}{uuid.uuid4().hex[:8]}"
            if candidate not in accounts and not self._credential_path(candidate).exists():
                return candidate
        raise RuntimeError("无法分配 Codex 账号连接 id")

    def _clear_models_cache_locked(self, account_id: str) -> bool:
        """删除该账号的模型缓存（best-effort）；失败不影响凭证/身份操作。"""
        try:
            if account_id == LEGACY_CODEX_ACCOUNT_ID:
                # 主账号缓存文件同时承载全局 proxy/client_version 配置：只清模型段，
                # 不删文件，避免登出顺带丢掉用户代理设置。
                from utils.api_client.codex.settings import load_codex_data, save_codex_data

                data = load_codex_data()
                if not data:
                    return True
                changed = False
                for key in ("models", "fetched_at", "source", "etag"):
                    if key in data:
                        data.pop(key, None)
                        changed = True
                if changed:
                    save_codex_data(data)
                return True
            from utils.api_client.codex.settings import CODEX_MODELS_DIR

            path = Path(CODEX_MODELS_DIR) / f"{account_id}.json"
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return True
        except OSError:
            return False

    def logout(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        """登出：删除该账号凭证文件 + 索引条目（真登出，不做墓碑）。

        凭证删除失败（权限/占用）会抛出 OSError，调用方须返回结构化错误；此
        时索引与凭证保持原样，不会出现幽灵账号。凭证/索引提交成功后再清模型
        缓存；缓存清理失败不回滚身份，也不影响其它账号。
        """
        with self._lock:
            resolved = self._resolve_account_id_locked(account_id)
            index = self._load_index_locked()
            path = self._credential_path(resolved)
            # 先删凭证再删索引：若删除失败（权限/占用）直接报错，账号保持完整，
            # 不会出现“索引已删但文件还在，下次加载又把账号自愈回来”的幽灵账号。
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            record = index["accounts"].pop(resolved, None)
            self._save_index_locked(index)
            self._managers.pop(resolved, None)
            models_cache_cleared = self._clear_models_cache_locked(resolved)
            if resolved == LEGACY_CODEX_ACCOUNT_ID:
                self._legacy_manager = None
            return {
                "id": resolved,
                "removed": bool(record),
                "models_cache_cleared": models_cache_cleared,
                "primary_account_id": self.primary_account_id_locked(),
            }


_STORE: Optional[CodexAccountStore] = None
_STORE_LOCK = threading.Lock()


def get_codex_account_store() -> CodexAccountStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = CodexAccountStore()
        return _STORE


__all__ = [
    "CodexAccountStore",
    "codex_account_identity",
    "identity_hash_from_material",
    "get_codex_account_store",
]
