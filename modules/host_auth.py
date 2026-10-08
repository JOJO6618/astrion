"""Optional, instance-wide Host web credentials. Missing file means no password."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import stat
import threading
from typing import Iterator

from modules.host_auth_hash import HASH_METHOD, check_host_hash, generate_host_hash
from utils.atomic_io import atomic_write_json

_MAX_FILE_BYTES = 65536
_WRITE_LOCK = threading.RLock()


class HostAuthError(ValueError):
    """Invalid credentials or unreadable configuration; never includes a password."""


class HostAuthChanged(HostAuthError):
    """The credential generation changed before a protected update committed."""


@dataclass(frozen=True)
class HostAuthState:
    enabled: bool = False
    password_hash: str = ""
    generation: str = ""


def host_auth_path(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser() / "host_auth.json"


def load_host_auth(data_dir: str | Path) -> HostAuthState:
    path = host_auth_path(data_dir)
    if path.is_symlink():
        raise HostAuthError("Host password configuration must not be a symbolic link")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        # A broken link is a damaged config, not an uninitialized instance.
        if path.is_symlink():
            raise HostAuthError("Host password configuration is unavailable") from None
        return HostAuthState()
    except OSError:
        raise HostAuthError("Host password configuration is unavailable") from None
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as fp:
            info = os.fstat(fp.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES:
                raise HostAuthError("Invalid Host password configuration")
            payload = json.load(fp)
        if (not isinstance(payload, dict) or type(payload.get("version")) is not int
                or payload.get("version") != 1):
            raise HostAuthError("Invalid Host password configuration")
        enabled = payload.get("enabled")
        password_hash = payload.get("password_hash")
        generation = payload.get("generation")
        if (type(enabled) is not bool or not isinstance(password_hash, str)
                or not isinstance(generation, str) or len(generation) < 16):
            raise HostAuthError("Invalid Host password configuration")
        parts = password_hash.split("$")
        if enabled and (len(parts) != 3 or parts[0] != HASH_METHOD
                        or not parts[1] or len(parts[2]) != 128
                        or any(c not in "0123456789abcdef" for c in parts[2])):
            raise HostAuthError("Invalid Host password configuration")
        return HostAuthState(enabled, password_hash, generation)
    except (OSError, UnicodeError, ValueError, TypeError):
        raise HostAuthError("Invalid Host password configuration") from None


def valid_password(password: object) -> bool:
    return isinstance(password, str) and 8 <= len(password) <= 1024


def verify_host_password(state: HostAuthState, password: object) -> bool:
    if not state.enabled or not valid_password(password):
        return False
    try:
        return check_host_hash(state.password_hash, password)
    except (ValueError, TypeError):
        raise HostAuthError("Invalid Host password configuration") from None


@contextmanager
def _credential_lock(data_dir: str | Path) -> Iterator[None]:
    """Serialize CLI resets and web disables across processes as well as threads."""
    with _WRITE_LOCK:
        directory = Path(data_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        lock_path = directory / ".host_auth.lock"
        if lock_path.is_symlink():
            raise HostAuthError("Invalid Host credential lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "r+b") as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise HostAuthError("Invalid Host credential lock")
            if os.name == "nt":
                import msvcrt
                if os.fstat(lock.fileno()).st_size == 0:
                    lock.write(b"\0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _save(data_dir: str | Path, *, enabled: bool, password_hash: str) -> HostAuthState:
    path = host_auth_path(data_dir)
    if path.is_symlink():
        raise HostAuthError("Host password configuration must not be a symbolic link")
    state = HostAuthState(enabled, password_hash, secrets.token_urlsafe(32))
    atomic_write_json(path, {"version": 1, "enabled": state.enabled,
                             "password_hash": state.password_hash,
                             "generation": state.generation})
    os.chmod(path, 0o600)
    return state


def set_host_password(data_dir: str | Path, password: str) -> HostAuthState:
    if not valid_password(password):
        raise HostAuthError("Password must contain 8 to 1024 characters")
    hashed = generate_host_hash(password)
    with _credential_lock(data_dir):
        # The OS script also repairs a damaged regular configuration.
        return _save(data_dir, enabled=True, password_hash=hashed)


def disable_host_password(
    data_dir: str | Path, password: object, *, expected_generation: str | None = None,
) -> HostAuthState:
    with _credential_lock(data_dir):
        state = load_host_auth(data_dir)
        if expected_generation is not None and state.generation != expected_generation:
            raise HostAuthChanged("Host credentials changed; please log in again")
        if not verify_host_password(state, password):
            raise HostAuthError("Incorrect Host password")
        return _save(data_dir, enabled=False, password_hash="")
