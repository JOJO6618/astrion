"""按 Vite manifest 为手写 HTML 入口注入带内容哈希的产物路径。

## 为什么需要

`static/*.html` 是手写源码，里面的产物引用是固定名（`/static/dist/assets/main.js`）。
而 Vite 产物自 2026-10-15 起带内容哈希（见 `vite.config.ts`），文件名本身就是版本。

固定名 + CDN/浏览器各自独立缓存，会出现「旧 main.js + 新 theme.js」的混搭，
浏览器直接抛模块导出错误：

    Uncaught SyntaxError: The requested module './theme.js' does not
    provide an export named 'e'

因为入口 chunk 与它 import 的 chunk 被分别缓存、分别过期、分别回源，一致性
没有任何机制保证。产物名带哈希后，URL 即版本，不可能混搭；代价是 HTML 里的
固定名引用需要在响应时替换成 manifest 里的真实文件名——就是这个模块。

## 替换规则（全部来自构建产物，不猜名字）

- manifest 中 `isEntry` 的 chunk：`/static/dist/assets/<name>.js`
  -> `/static/dist/<file>`（`name` 是 rollupOptions.input 的键）
- manifest 中所有 CSS：剥掉内容哈希得到固定名后建立映射
  （`assets/auth-1a2b3c4d.css` -> `assets/auth.css`）
- 装配样式：`static/dist/.vite/entry-styles.json`（由
  `scripts/generate_entry_styles.mjs` 写出）里的 `/static/style.css` 键

映射解析不出来时**显式报错**（不做静默回退）：一旦 dist 与 manifest 不是同一次
构建，回退只会变成难查的 404，报错才能一眼看出是部署不完整。

例外：引用的文件在 dist 里确实存在（例如尚未切换的旧构建仍是固定名产物）时
原样保留。这样「新代码 + 旧 dist」与「旧代码 + 新 dist」都不会出错，部署顺序
（先代码还是先 dist）不影响可用性。

HTML 响应统一 `Cache-Control: no-store`，避免引用本身被缓存住。
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from flask import abort, current_app, make_response

# HTML 里可能出现的产物引用（只关心 js/css/mjs，其它静态资源不受哈希化影响）
_REF_RE = re.compile(r"/static/[A-Za-z0-9._/-]+?\.(?:js|css|mjs)")

# 内容哈希片段：Vite 默认 8 位（vite.config.ts 里显式写 [hash:8] 与之对应）
_HASH_RE = re.compile(r"^(?P<base>.+)-(?P<digest>[A-Za-z0-9_-]{8})\.(?P<ext>[^.]+)$")

# 映射表缓存：(stamp, mapping)；stamp 变化说明 dist 被换了，重新解析
_CACHE_LOCK = threading.RLock()
_MAP_CACHE: Dict[str, object] = {}


class EntryAssetError(RuntimeError):
    """产物映射解析失败（HTML 引用了 manifest 中不存在的产物）。"""


def _strip_content_hash(rel_path: str) -> Optional[str]:
    """`assets/auth-1a2b3c4d.css` -> `assets/auth.css`；无哈希返回 None。"""
    directory, _, name = rel_path.rpartition("/")
    match = _HASH_RE.match(name)
    if not match:
        return None
    base = f"{match.group('base')}.{match.group('ext')}"
    return f"{directory}/{base}" if directory else base


def _dist_dir() -> Path:
    return Path(current_app.static_folder or "static") / "dist"


def _read_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EntryAssetError(f"无法解析 {path.name}：{error}") from error
    return payload if isinstance(payload, dict) else None


def _build_map(dist: Path) -> Dict[str, str]:
    manifest = _read_json(dist / ".vite" / "manifest.json")
    if manifest is None:
        raise EntryAssetError(
            "缺少 static/dist/.vite/manifest.json，无法为 HTML 注入产物路径"
            "（dist 可能没有部署完整或未执行过 npm run build）。"
        )

    mapping: Dict[str, str] = {}

    def add(ref: str, target: str) -> None:
        existing = mapping.get(ref)
        if existing and existing != target:
            raise EntryAssetError(
                f"产物映射冲突：{ref} 同时指向 {existing} 与 {target}"
            )
        mapping[ref] = target

    for chunk in manifest.values():
        if not isinstance(chunk, dict):
            continue
        file_ref = chunk.get("file")
        if not isinstance(file_ref, str) or not file_ref:
            continue
        name = chunk.get("name")
        if chunk.get("isEntry") and isinstance(name, str) and name:
            add(f"/static/dist/assets/{name}.js", f"/static/dist/{file_ref}")
        for css in chunk.get("css") or []:
            if not isinstance(css, str):
                continue
            fixed = _strip_content_hash(css)
            if fixed:
                add(f"/static/dist/{fixed}", f"/static/dist/{css}")

    assemblies = _read_json(dist / ".vite" / "entry-styles.json")
    if assemblies:
        for ref, file_name in (assemblies.get("assemblies") or {}).items():
            if isinstance(ref, str) and isinstance(file_name, str) and file_name:
                add(ref, f"/static/dist/{file_name}")

    return mapping


def _stamp(dist: Path) -> Tuple:
    def stat_of(path: Path) -> Tuple[int, int]:
        try:
            info = path.stat()
        except OSError:
            return (0, 0)
        return (info.st_mtime_ns, info.st_size)

    vite_dir = dist / ".vite"
    return (stat_of(vite_dir / "manifest.json"), stat_of(vite_dir / "entry-styles.json"))


def _get_map() -> Dict[str, str]:
    dist = _dist_dir()
    stamp = _stamp(dist)
    with _CACHE_LOCK:
        cached_stamp = _MAP_CACHE.get("stamp")
        if cached_stamp == stamp and isinstance(_MAP_CACHE.get("map"), dict):
            return _MAP_CACHE["map"]  # type: ignore[return-value]
    mapping = _build_map(dist)
    with _CACHE_LOCK:
        _MAP_CACHE["stamp"] = stamp
        _MAP_CACHE["map"] = mapping
    return mapping


def _exists_in_dist(ref: str) -> bool:
    """引用的路径是否是 dist 下真实存在的文件（旧构建可能是固定名产物）。"""
    relative = ref[len("/static/dist/") :]
    if not relative or ".." in relative:
        return False
    return (_dist_dir() / relative).is_file()


def inject_entry_assets(html: str) -> str:
    """把 HTML 中的固定名产物引用替换为 manifest 里的真实文件名。"""
    mapping = _get_map()
    unresolved = []

    def _replace(match: "re.Match[str]") -> str:
        ref = match.group(0)
        target = mapping.get(ref)
        if target:
            return target
        # dist 命名空间下、映射没命中、文件也不存在：说明 dist 与 manifest
        # 不是同一次构建（或引用写错），这时才报错。
        if ref.startswith("/static/dist/") and not _exists_in_dist(ref):
            unresolved.append(ref)
        return ref

    result = _REF_RE.sub(_replace, html)
    if unresolved:
        unique = ", ".join(sorted(set(unresolved)))
        raise EntryAssetError(
            f"HTML 引用了 dist 中不存在的产物：{unique}"
            "（dist 与 manifest 可能不是同一次构建，或部署未完成）"
        )
    return result


def serve_entry_html(name: str):
    """替代 `send_static_file` 的 HTML 出口：注入产物哈希 + 禁止缓存。"""
    path = Path(current_app.static_folder or "static") / name
    try:
        html = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        abort(404)
    except OSError as error:
        raise EntryAssetError(f"无法读取 HTML 入口 {name}：{error}") from error

    response = make_response(inject_entry_assets(html))
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Cache-Control"] = "no-store"
    return response


__all__ = ["EntryAssetError", "inject_entry_assets", "serve_entry_html"]
