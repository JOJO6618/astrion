"""Inspect/decrypt one local Chrome profile in memory; reports contain counts only."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import subprocess
import time
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

REPO = Path(__file__).resolve().parents[2]
ROOT = Path.home() / "Library/Application Support/Google/Chrome"
PREFIX = re.compile(rb"v\d\d")
PROGRESS: Path | None = None
STAGE = "initializing"
SNAPSHOTS: list[dict[str, Any]] = []


def trace(stage: str) -> None:
    global STAGE
    STAGE = stage
    if PROGRESS is not None:
        PROGRESS.write_text(json.dumps({"stage": stage, "at": time.time()}))
        PROGRESS.chmod(0o600)
    print(json.dumps({"stage": stage}), flush=True)


def snapshot(file: Path) -> sqlite3.Connection:
    source = sqlite3.connect(file.as_uri() + "?mode=ro", uri=True, timeout=5)
    target = sqlite3.connect(":memory:")
    started = time.monotonic()
    diagnostic: dict[str, Any] = {"stage": STAGE, "source_bytes": file.stat().st_size, "callbacks": 0}
    SNAPSHOTS.append(diagnostic)

    def progress(status: int, remaining: int, total: int) -> None:
        diagnostic.update({"status": status, "remaining_pages": remaining, "total_pages": total})
        diagnostic["callbacks"] += 1
        if time.monotonic() - started > 8:
            raise sqlite3.OperationalError("snapshot_deadline")
    try:
        source.execute("PRAGMA query_only=ON")
        # Pin a WAL read snapshot before backup so ongoing writes cannot restart it.
        source.execute("BEGIN")
        source.execute("SELECT count(*) FROM sqlite_schema").fetchone()
        source.backup(target, pages=256, sleep=0.02, progress=progress)
        diagnostic["completed"] = True
    except BaseException:
        diagnostic["completed"] = False
        target.close()
        raise
    finally:
        diagnostic["elapsed_seconds"] = round(time.monotonic() - started, 3)
        source.close()
    target.row_factory = sqlite3.Row
    return target


def prefix(value: bytes) -> str:
    return value[:3].decode("ascii") if PREFIX.fullmatch(value[:3]) else "empty" if not value else "other"


def decrypt(value: bytes, key: bytes, domain: str | None = None, version: int = 0) -> str:
    if not value.startswith(b"v10"):
        raise ValueError("unsupported_prefix")
    if len(value) <= 3 or (len(value) - 3) % 16:
        raise ValueError("invalid_ciphertext")
    decoder = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    decrypted = decoder.update(value[3:]) + decoder.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        plain = unpadder.update(decrypted) + unpadder.finalize()
    except ValueError:
        raise ValueError("padding_failed") from None
    if domain is not None and version >= 24:
        if not plain.startswith(hashlib.sha256(domain.encode()).digest()):
            raise ValueError("domain_hash_failed")
        plain = plain[32:]
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("utf8_failed") from None


def synthetic_test() -> dict[str, bool]:
    key = hashlib.pbkdf2_hmac("sha1", b"synthetic-key-material", b"saltysalt", 1003, 16)
    def encrypt(value: bytes) -> bytes:
        padder = padding.PKCS7(128).padder()
        padded = padder.update(value) + padder.finalize()
        encoder = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
        return b"v10" + encoder.update(padded) + encoder.finalize()
    password = decrypt(encrypt(b"synthetic-password"), key) == "synthetic-password"
    domain = ".example.test"
    cookie = decrypt(encrypt(hashlib.sha256(domain.encode()).digest() + b"synthetic-cookie"), key, domain, 24) == "synthetic-cookie"
    rejected = False
    try:
        decrypt(encrypt(hashlib.sha256(domain.encode()).digest() + b"synthetic-cookie"), key, ".other.test", 24)
    except ValueError as error:
        rejected = str(error) == "domain_hash_failed"
    return {"password_roundtrip": password, "cookie_v24_roundtrip": cookie, "wrong_domain_rejected": rejected}


def chrome_version() -> str:
    candidates = [Path("/Applications/Google Chrome.app/Contents/Info.plist"), Path.home() / "Applications/Google Chrome.app/Contents/Info.plist"]
    for file in candidates:
        if file.is_file():
            with file.open("rb") as stream:
                return str(plistlib.load(stream).get("CFBundleShortVersionString", "unknown"))
    return "not_found"


def running() -> bool:
    process = subprocess.run(["/bin/ps", "-axo", "uid=,comm="], capture_output=True, text=True, check=False)
    for line in process.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0] == str(os.getuid()) and re.search(r"Google Chrome(?: Helper[^/]*)?$", parts[1]):
            return True
    return False


def inspect(profile: Path, report: dict[str, Any]) -> tuple[list[bytes], list[tuple[str, bytes]], int]:
    encrypted_passwords: list[bytes] = []
    encrypted_cookies: list[tuple[str, bytes]] = []
    report["passwords"] = {"databases": [], "encryption_prefixes": {}}
    prefixes: collections.Counter[str] = collections.Counter()
    for name in ["Login Data", "Login Data For Account"]:
        file = profile / name
        if not file.is_file():
            continue
        trace(f"snapshot:{name}")
        database = snapshot(file)
        try:
            columns = [row[1] for row in database.execute("PRAGMA table_info(logins)")]
            rows = database.execute("SELECT password_value FROM logins").fetchall()
            values = [bytes(row[0] or b"") for row in rows]
            prefixes.update(prefix(value) for value in values)
            encrypted_passwords.extend(value for value in values if value)
            report["passwords"]["databases"].append({"name": name, "rows": len(rows), "columns": columns,
                "blocked_rows": database.execute("SELECT count(*) FROM logins WHERE blacklisted_by_user<>0").fetchone()[0],
                "non_html_scheme_rows": database.execute("SELECT count(*) FROM logins WHERE scheme<>0").fetchone()[0]})
        finally:
            database.close()
    report["passwords"]["encryption_prefixes"] = dict(prefixes)
    cookie_version = 0
    file = next((profile / name for name in ["Network/Cookies", "Cookies"] if (profile / name).is_file()), None)
    report["cookies"] = {"database_present": file is not None}
    if file:
        trace("snapshot:Cookies")
        database = snapshot(file)
        try:
            columns = [row[1] for row in database.execute("PRAGMA table_info(cookies)")]
            row = database.execute("SELECT value FROM meta WHERE key='version'").fetchone()
            cookie_version = int(row[0]) if row else 0
            rows = database.execute("SELECT host_key, encrypted_value, length(value)>0 AS plain_value FROM cookies").fetchall()
            encrypted_cookies = [(row[0], bytes(row[1])) for row in rows if row[1]]
            report["cookies"].update({"rows": len(rows), "schema_version": cookie_version, "columns": columns,
                "encryption_prefixes": dict(collections.Counter(prefix(value) for _, value in encrypted_cookies)),
                "plaintext_rows": sum(bool(row[2]) for row in rows),
                "conflicting_storage_rows": sum(bool(row[1]) and bool(row[2]) for row in rows)})
            if "top_frame_site_key" in columns:
                report["cookies"]["partitioned_rows"] = database.execute("SELECT count(*) FROM cookies WHERE top_frame_site_key<>''").fetchone()[0]
        finally:
            database.close()
    file = profile / "History"
    report["history"] = {"database_present": file.is_file()}
    if file.is_file():
        trace("snapshot:History")
        database = None
        try:
            database = snapshot(file)
            report["history"]["url_rows"] = database.execute("SELECT count(*) FROM urls").fetchone()[0]
            report["history"]["visit_rows"] = database.execute("SELECT count(*) FROM visits").fetchone()[0]
            report["history"]["snapshot_available"] = True
        except sqlite3.Error as error:
            report["history"].update({"snapshot_available": False,
                "error_category": "snapshot_deadline" if str(error) == "snapshot_deadline" else "sqlite_error",
                "sqlite_error_name": getattr(error, "sqlite_errorname", None)})
        finally:
            if database is not None:
                database.close()
    return encrypted_passwords, encrypted_cookies, cookie_version


def main() -> int:
    global PROGRESS
    parser = argparse.ArgumentParser()
    parser.add_argument("--decrypt", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--profile")
    args = parser.parse_args()
    report: dict[str, Any] = {"synthetic": synthetic_test(), "read_only_source": True, "database_snapshot": "memory_only"}
    if args.self_test:
        print(json.dumps(report, indent=2))
        return 0 if all(report["synthetic"].values()) else 1
    destination = REPO / "_experiments/chrome-export-check" / str(time.time_ns())
    destination.mkdir(parents=True, mode=0o700)
    PROGRESS = destination / "progress.json"
    try:
        trace("read:Local State")
        state = json.loads((ROOT / "Local State").read_text())
        profiles = [row.name for row in ROOT.iterdir() if row.is_dir() and re.fullmatch(r"Default|Profile \d+", row.name)]
        chosen = args.profile or state.get("profile", {}).get("last_used", "Default")
        if chosen not in profiles:
            raise ValueError("selected_profile_missing")
        trace("inspect:Chrome version and running state")
        report.update({"chrome_version": chrome_version(), "chrome_running": running(), "profile_count": len(profiles), "selected_profile_id": chosen})
        passwords, cookies, version = inspect(ROOT / chosen, report)
        trace("inspect:databases complete")
        if args.decrypt and (passwords or cookies):
            trace("keychain:read-only query")
            secret_result = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "Chrome Safe Storage", "-a", "Chrome", "-w"], capture_output=True, timeout=300, check=False)
            if secret_result.returncode:
                report["keychain"] = {"access": "not_granted", "exit_code": secret_result.returncode}
            else:
                report["keychain"] = {"access": "granted"}
                secret = secret_result.stdout[:-1] if secret_result.stdout.endswith(b"\n") else secret_result.stdout
                key = hashlib.pbkdf2_hmac("sha1", secret, b"saltysalt", 1003, 16)
                for label, values in [("password_decryption", [(None, value) for value in passwords]), ("cookie_decryption", cookies)]:
                    trace(f"decrypt:{label}")
                    outcomes: collections.Counter[str] = collections.Counter()
                    for domain, value in values:
                        try:
                            plain = decrypt(value, key, domain, version)
                            outcomes["success" if plain else "empty_value"] += 1
                            plain = ""
                        except ValueError as error:
                            outcomes[str(error)] += 1
                    report[label] = dict(outcomes)
                secret = key = b""
                secret_result = None
        report["completed"] = True
        report["all_requested_checks_passed"] = (
            all(report["synthetic"].values())
            and report["history"].get("snapshot_available", True)
            and (not args.decrypt or not (passwords or cookies) or (
                report.get("keychain", {}).get("access") == "granted"
                and all(set(report.get(label, {})) <= {"success", "empty_value"}
                        for label in ["password_decryption", "cookie_decryption"]))))
    except (OSError, sqlite3.Error, ValueError, subprocess.TimeoutExpired) as error:
        # Never serialize subprocess stdout/stderr, decrypted strings, domains or account names.
        report["completed"] = False
        report["error_type"] = type(error).__name__
        report["error_stage"] = STAGE
        if isinstance(error, sqlite3.Error):
            report["error_category"] = "snapshot_deadline" if str(error) == "snapshot_deadline" else "sqlite_error"
            report["sqlite_error_name"] = getattr(error, "sqlite_errorname", None)
        elif isinstance(error, subprocess.TimeoutExpired):
            report["error_category"] = "subprocess_timeout"
        elif isinstance(error, OSError):
            report["error_category"] = "filesystem_error"
            report["errno"] = error.errno
        else:
            report["error_category"] = "validation_error"
    report["snapshots"] = SNAPSHOTS
    file = destination / "summary.json"
    file.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    file.chmod(0o600)
    print(json.dumps({**report, "summary": str(file.relative_to(REPO))}, ensure_ascii=False, indent=2))
    return 0 if report.get("completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
