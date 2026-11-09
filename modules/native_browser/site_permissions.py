"""「以后都允许」的站点列表：按用户保存在数据目录 browser_site_permissions.json。

站点字符串由 Electron 统一计算（可注册域名含子域名；localhost/IP 按主机＋端口），
这里只做存取与格式校验。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

FILE_NAME = "browser_site_permissions.json"
_LOCK = threading.RLock()
_SITE_RE = re.compile(r"^(?:[a-z0-9-]+\.)*[a-z0-9-]+(?::\d{1,5})?$|^\[[0-9a-f:.]+\]:\d{1,5}$")


def _path(data_dir: Any) -> Path:
    return Path(str(data_dir)) / FILE_NAME


def valid_site(site: Any) -> bool:
    return isinstance(site, str) and 0 < len(site) <= 260 and bool(_SITE_RE.match(site))


def _read(data_dir: Any) -> Dict[str, Any]:
    path = _path(data_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "sites": {}}
    except (OSError, ValueError):
        return {"version": 1, "sites": {}}
    sites = data.get("sites") if isinstance(data, dict) else None
    if not isinstance(sites, dict):
        sites = {}
    return {"version": 1, "sites": {k: v for k, v in sites.items() if valid_site(k) and isinstance(v, dict)}}


def _write(data_dir: Any, data: Dict[str, Any]) -> None:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".browser_sites.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def allowed_sites(data_dir: Any) -> List[str]:
    with _LOCK:
        return sorted(_read(data_dir)["sites"].keys())


def list_sites(data_dir: Any) -> List[Dict[str, Any]]:
    with _LOCK:
        sites = _read(data_dir)["sites"]
    return [{"site": site, "granted_at": info.get("granted_at")} for site, info in sorted(sites.items())]


def grant_site(data_dir: Any, site: str) -> bool:
    if not valid_site(site):
        return False
    with _LOCK:
        data = _read(data_dir)
        if site not in data["sites"]:
            data["sites"][site] = {"granted_at": time.time()}
            _write(data_dir, data)
    return True


def revoke_site(data_dir: Any, site: str) -> bool:
    with _LOCK:
        data = _read(data_dir)
        if site not in data["sites"]:
            return False
        data["sites"].pop(site, None)
        _write(data_dir, data)
    return True
