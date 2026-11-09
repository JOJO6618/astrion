"""浏览器工具的权限评估与执行（主对话 WebTerminal 调用）。"""
from __future__ import annotations

import asyncio
import base64
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit
from urllib.request import url2pathname

from modules.native_browser import client
from modules.native_browser.availability import native_browser_supported
from modules.native_browser.site_permissions import allowed_sites
from modules.native_browser.state import browser_tools_enabled, snapshot_epoch
from modules.native_browser.tools import ALWAYS_APPROVE_OPS, is_action, op_of
from utils.logger import setup_logger

logger = setup_logger(__name__)

DISABLED_MESSAGE = "浏览器未开启：用户可在输入栏「+」菜单中打开「浏览器」后再使用浏览器工具。现在不要重试，如确有需要可请用户开启。"
UNSUPPORTED_MESSAGE = "内置浏览器工具仅在 macOS/Windows 桌面版的 Agent 主对话中可用。"
READONLY_MESSAGE = "当前为只读（或计划）模式：浏览器只能使用读取类工具（导航、快照、查找、截图、滚动、悬停等），不能点击、输入或执行脚本。"


def _identity(terminal: Any) -> Dict[str, str]:
    cm = getattr(terminal, "context_manager", None)
    return {
        "username": str(getattr(terminal, "username", "") or ""),
        "workspace_id": str(getattr(terminal, "workspace_id", "") or ""),
        "conversation_id": str(getattr(cm, "current_conversation_id", "") or ""),
    }


def _data_dir(terminal: Any) -> str:
    return str(getattr(terminal, "data_dir", "") or "")


def _prefs(terminal: Any) -> Dict[str, Any]:
    try:
        from modules.personalization_manager import load_personalization_config
        return load_personalization_config(_data_dir(terminal)) or {}
    except Exception:
        return {}


def preflight(terminal: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """审批前查询目标标签当前站点（同步、快速；不进入标签串行队列）。"""
    payload = {"call_id": f"site_{uuid.uuid4().hex}", "op": "site", "identity": _identity(terminal),
               "args": {"tab_id": arguments.get("tab_id")} if arguments.get("tab_id") else {}}
    try:
        return client.call(payload, timeout=5.0)
    except client.BridgeError as exc:
        return {"success": False, "error": str(exc)}


def evaluate_permission(terminal: Any, tool_name: str, arguments: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """返回与 evaluate_tool_permission 同结构的结果。读取类不审批；动作类按站点审批。"""
    if not native_browser_supported() or not browser_tools_enabled(terminal):
        # 执行时会返回明确错误，不在这里弹审批。
        return {"allowed": True, "mode": mode}
    pre: Dict[str, Any] = {}
    if op_of(tool_name) == "handle_dialog" and arguments.get("accept") is True:
        pre = preflight(terminal, arguments)
    if not is_action(tool_name, arguments, (pre.get("dialog") or {}).get("type")):
        return {"allowed": True, "mode": mode}
    if mode == "readonly":
        return {"allowed": False, "mode": mode, "code": "readonly_denied", "message": READONLY_MESSAGE}
    if mode == "unrestricted":
        return {"allowed": True, "mode": mode}
    if not pre:
        pre = preflight(terminal, arguments)
    site = str(pre.get("site") or "") if pre.get("success") else ""
    if not site:
        # 无法确定站点（无标签、空白页或通道故障）：不弹审批，执行时由站点校验拒绝。
        return {"allowed": True, "mode": mode}
    needs = op_of(tool_name) in ALWAYS_APPROVE_OPS or site not in allowed_sites(_data_dir(terminal))
    if not needs:
        return {"allowed": True, "mode": mode}
    return {
        "allowed": True, "mode": mode, "requires_approval": True, "approval_type": "browser_action",
        "browser_site": site, "browser_url": str(pre.get("url") or ""),
    }


def _download_dir(terminal: Any, prefs: Dict[str, Any]) -> str:
    project = Path(str(getattr(getattr(terminal, "context_manager", None), "project_path", "") or ".")).resolve()
    raw = str(prefs.get("browser_download_dir") or "").strip()
    target = Path(raw).expanduser() if raw else Path(".astrion/browser_downloads")
    if not target.is_absolute():
        target = project / target
    return str(target)


def _readable_paths(terminal: Any, paths: Any) -> List[str]:
    if paths is None:
        return []
    if not isinstance(paths, list) or len(paths) > 20 or any(not isinstance(p, str) or not p.strip() for p in paths):
        raise ValueError("paths 必须是不超过 20 项的文件路径数组。")
    from modules.execution_plane import get_execution_backend
    files = get_execution_backend(terminal).files
    project = Path(str(terminal.context_manager.project_path)).resolve()
    result = []
    for raw in paths:
        info = files.resolve_path(raw.strip(), access="read")
        if not info.get("success"):
            raise ValueError(f"文件不在可读范围内：{raw}（{info.get('error') or '无权限'}）")
        resolved = Path(info["path"])
        result.append(str(resolved if resolved.is_absolute() else (project / resolved)))
    return result


def _file_navigation(terminal: Any, value: str) -> tuple[str, List[str]]:
    """Grant only files checked by the execution backend, for this bridge call."""
    if len(value) > 8192 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("本地文件 URL 包含无效字符。")
    url = urlsplit(value)
    if (url.scheme.lower() != "file" or url.netloc.lower() not in {"", "localhost"}
            or "\\" in url.path or re.search(r"%(?:2f|5c)|%(?![0-9a-f]{2})", url.path, re.I)):
        raise ValueError("只允许本机 file:// 文件地址，不支持远程主机或 UNC 路径。")
    path = Path(url2pathname(url.path))
    if (not path.is_absolute() or str(path).startswith(("\\\\", "//"))
            or any(ord(char) < 32 or ord(char) == 127 for char in str(path))):
        raise ValueError("file:// 必须指向有效的本机绝对路径。")
    resolved = Path(_readable_paths(terminal, [str(path)])[0])
    target = urlsplit(resolved.as_uri())
    return urlunsplit((target.scheme, target.netloc, target.path, url.query, url.fragment)), [path.as_uri(), resolved.as_uri()]


def _save_screenshot(terminal: Any, data: str) -> str:
    project = Path(str(terminal.context_manager.project_path)).resolve()
    folder = project / ".astrion" / "browser_screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:6]}.png"
    path = folder / name
    path.write_bytes(base64.b64decode(data))
    return str(path.relative_to(project))


async def execute(terminal: Any, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if not native_browser_supported():
        return {"success": False, "code": "browser_unsupported", "error": UNSUPPORTED_MESSAGE}
    if not browser_tools_enabled(terminal):
        return {"success": False, "code": "browser_disabled", "error": DISABLED_MESSAGE}
    from modules.execution_scope import current_execution_scope
    scope = current_execution_scope()
    if scope is not None and scope.is_sub_agent:
        return {"success": False, "code": "browser_unsupported", "error": UNSUPPORTED_MESSAGE}
    op = op_of(tool_name)
    args = {k: v for k, v in (arguments or {}).items() if k != "intent"}
    prefs = _prefs(terminal)
    try:
        mode = terminal.get_permission_mode()
    except Exception:
        mode = "readonly"
    payload: Dict[str, Any] = {
        "call_id": f"browser_{uuid.uuid4().hex}", "op": op, "args": args, "identity": _identity(terminal),
        "epoch": snapshot_epoch(terminal), "reveal": prefs.get("browser_auto_open", True) is not False,
        "download_dir": _download_dir(terminal, prefs),
    }
    if not all(payload["identity"].values()):
        return {"success": False, "code": "browser_identity", "error": "当前对话尚未创建完成，暂时不能使用浏览器。"}
    # 动作授权：Electron 在发出输入前核对页面当前站点（审批与执行之间页面可能已跳转）。
    approved = getattr(scope, "browser_site", None) if scope is not None else None
    if mode == "unrestricted":
        payload["any_site"] = True
    elif mode == "readonly":
        payload["allowed_sites"] = []
    elif op in ALWAYS_APPROVE_OPS:
        payload["allowed_sites"] = [approved] if approved else []
    else:
        payload["allowed_sites"] = sorted(set(allowed_sites(_data_dir(terminal))) | ({approved} if approved else set()))
    try:
        file_urls: List[str] = []
        if op == "navigate" or (op == "tabs" and args.get("action") == "new"):
            target = args.get("url")
            if isinstance(target, str) and target.strip().lower().startswith("file:"):
                args["url"], granted = _file_navigation(terminal, target.strip())
                file_urls.extend(granted)
        if op != "tabs" or args.get("action") == "select":
            current = await asyncio.to_thread(preflight, terminal, args)
            current_url = current.get("url")
            if isinstance(current_url, str) and current_url.lower().startswith("file:"):
                _, granted = _file_navigation(terminal, current_url)
                file_urls.extend(granted)
        payload["file_urls"] = sorted(set(file_urls))
    except (ValueError, OSError) as exc:
        return {"success": False, "code": "path_not_allowed", "error": str(exc)}
    if op in {"file_upload", "drop"}:
        try:
            readable = _readable_paths(terminal, args.get("paths"))
        except ValueError as exc:
            return {"success": False, "code": "path_not_allowed", "error": str(exc)}
        payload["args"]["paths"] = readable
        payload["readable_paths"] = readable
    if op in ALWAYS_APPROVE_OPS and mode not in {"unrestricted"} and not approved:
        return {"success": False, "code": "approval_required", "error": f"{tool_name} 每次都需要审批。"}
    try:
        result = await asyncio.to_thread(client.call, payload, 150.0)
    except asyncio.CancelledError:
        await asyncio.to_thread(client.cancel, payload["call_id"])
        raise
    except client.BridgeError as exc:
        return {"success": False, "code": "browser_unavailable", "error": str(exc)}
    if not isinstance(result, dict):
        return {"success": False, "code": "browser_error", "error": "内置浏览器返回了无效结果。"}
    if result.get("code") == "site_not_approved" and mode in {"approval", "auto_approval"}:
        result["error"] = f"{result.get('error') or ''} 站点需要先经过审批；如页面已跳转，重新调用该工具会针对新站点发起审批。".strip()
    image = result.pop("image_base64", None)
    if image and result.get("success") is not False:
        try:
            rel = _save_screenshot(terminal, image)
        except Exception as exc:
            logger.warning("保存浏览器截图失败: %s", exc)
            return {"success": False, "code": "screenshot_save_failed", "error": f"截图保存失败：{exc}"}
        result["screenshot_path"] = rel
        try:
            from config.model_profiles import model_supports_image
            if model_supports_image(getattr(terminal, "model_key", "") or ""):
                setattr(terminal, "pending_image_view", {"path": rel})
                result["image_attached"] = True
        except Exception:
            pass
    result["op"] = op
    return result
