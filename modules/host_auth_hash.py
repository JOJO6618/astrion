"""Werkzeug-compatible scrypt hashes without relying on hashlib.scrypt."""
from __future__ import annotations

import hmac

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from werkzeug.security import gen_salt

HASH_METHOD = "scrypt:32768:8:1"


def _derive(password: str, salt: str) -> str:
    # Werkzeug uses UTF-8, a 64-byte derived key and these same parameters.
    return Scrypt(
        salt=salt.encode("utf-8"), length=64, n=32768, r=8, p=1,
    ).derive(password.encode("utf-8")).hex()


def generate_host_hash(password: str) -> str:
    salt = gen_salt(16)
    return f"{HASH_METHOD}${salt}${_derive(password, salt)}"


def check_host_hash(password_hash: str, password: str) -> bool:
    parts = password_hash.split("$")
    if len(parts) != 3 or parts[0] != HASH_METHOD or not parts[1]:
        raise ValueError("Invalid Host password hash")
    expected = parts[2]
    if len(expected) != 128 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("Invalid Host password hash")
    return hmac.compare_digest(_derive(password, parts[1]), expected)
