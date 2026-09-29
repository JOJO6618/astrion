"""预览面板目标检测与记录（2026-09-27 新增，2026-09-29 简化）。

两类捕获源（实时检测）：
1. write_file / edit_file 创建/修改的 .html 文件（tools_execution 挂钩）
2. 模型流式输出中的 localhost / 127.0.0.1 / [::1] 链接（stream_loop 滚动缓冲增量匹配）

2026-09-29 简化（用户拍板）：删除 run_command / terminal_input / terminal_snapshot
三处命令输出挂钩——实测它们是脏数据主来源（浏览器控制台日志 `@ url:行号` 后缀、
代码模板字符串 f"http://127.0.0.1:{port}/" 假地址），且放行条件里「输出非空」
几乎恒真，门禁形同虚设。服务器地址改由 prompt 要求模型在回复中主动给出完整
URL（prompts/preview_panel.txt），模型输出源负责捕捉。

URL 识别规则（2026-09-29 重写，修复 Markdown/中文粘连）：
- 路径采用 ASCII 白名单字符集（排除 `* ' " ( ) [ ] { } < > :` 与一切非 ASCII），
  Markdown 加粗符、中文、全角括号自然截断——旧黑名单字符集曾把
  `index.html**（预览面板里也有）` 整段粘进 URL 并生成多条脏记录；
- 裸主机（无端口且无路径）不记录——真实 dev server 必有端口，裸主机几乎
  都是代码模板（f"http://127.0.0.1:{port}/"）或散文提及；
- favicon.ico 等静态资源请求不记录（控制台 404 噪音）。

存储：对话 metadata.preview_targets（跟随对话，压缩/重启不丢），结构见 _normalize。
广播：与 edited_files 同款——写 metadata 后经 context_manager 回调发
"preview_targets_updated" 事件（轮询事件流，Socket.IO 移除后的唯一实时通道）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

# 本地 URL 匹配：localhost / 127.0.0.1 / [::1]，可选端口与路径
# 0.0.0.0 是监听地址（python -m http.server 等横幅打印它），匹配后归一化为 127.0.0.1
# 路径 = ASCII 白名单（2026-09-29）：Markdown/中文/全角符号/行号后缀一概不粘；
# 代码模板 f"http://127.0.0.1:{port}/" 匹配不到数字端口，落成裸主机后由
# _accept_url 拒绝，无需额外的结尾前瞻
_LOCAL_URL_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d{1,5})?"
    r"(?:/[A-Za-z0-9\-._~%/@?#=&+]*)?",
    re.IGNORECASE,
)

# URL 结构解析（判裸主机用）：host + 可选端口 + 可选路径
_URL_STRUCTURE_RE = re.compile(
    r"^(https?://(?:localhost|127\.0\.0\.1|\[::1\]))(:\d{1,5})?(/.*)?$",
    re.IGNORECASE,
)

# 静态资源噪音路径（浏览器自动请求，不是用户要预览的页面）
_NOISE_PATH_SUFFIXES = ("/favicon.ico",)


def _normalize_url(url: str) -> str:
    """URL 归一化：0.0.0.0 是监听地址不是访问地址（Chrome 已禁止访问 0.0.0.0），
    预览统一映射为 127.0.0.1。"""
    return re.sub(r"^(https?://)0\.0\.0\.0", r"\g<1>127.0.0.1", url, flags=re.IGNORECASE)


_HTML_SUFFIXES = (".html", ".htm")


def is_preview_enabled() -> bool:
    """预览面板总开关（2026-09-27 用户拍板）：仅 host 模式启用。

    docker/web 是唯一把端口暴露到公网的部署形态，红队演练（见项目记忆
    preview_security_redteam）确认同源回退路径存在会话级注入风险，
    且独立预览服务器（127.0.0.1）远端浏览器不可达——故 docker 模式整体
    禁用：不检测、不记录、端点拒绝服务、前端无目标可显示。
    """
    try:
        from config.paths import IS_HOST_MODE
        return bool(IS_HOST_MODE)
    except Exception:
        return False


# 预览目标数量上限（防御性，正常对话远低于此）
MAX_PREVIEW_TARGETS = 100

# 流式文本增量扫描的滚动缓冲长度（URL 可能横跨两个 chunk）
STREAM_SCAN_OVERLAP = 120


def is_previewable_file(path: Any) -> bool:
    rel = str(path or "").strip().lower()
    return rel.endswith(_HTML_SUFFIXES)


def _accept_url(url: str) -> Optional[str]:
    """URL 有效性判定 + 规范化。返回可记录的 URL，无效返回 None。

    - 剥离尾部残留标点（正则白名单之外的兜底，如句末英文句号紧贴）；
    - 尾部 `/` 归一（`…:8321/` 与 `…:8321` 同一条）；
    - 裸主机（无端口且无路径）拒绝；
    - favicon.ico 等静态资源噪音拒绝。
    """
    url = _normalize_url(url.rstrip(".,;:!?")).rstrip("/")
    match = _URL_STRUCTURE_RE.match(url)
    if not match:
        return None
    _host, port, path = match.group(1), match.group(2), match.group(3)
    if not port and not path:
        return None  # 裸主机：无端口无路径，无预览价值（多为代码模板残留）
    if path and path.lower().endswith(_NOISE_PATH_SUFFIXES):
        return None
    return url


def scan_text_for_local_urls(text: str) -> List[str]:
    """从文本中提取本地 URL 列表（去重、保序）。"""
    if not text:
        return []
    urls: List[str] = []
    for match in _LOCAL_URL_RE.finditer(text):
        url = _accept_url(match.group(0))
        if url and url not in urls:
            urls.append(url)
    return urls


def scan_stream_urls(buffer: str, seen: set) -> List[str]:
    """流式增量扫描：返回本轮可确认的完整 URL 列表。

    触及 buffer 末尾的匹配可能是被 chunk 边界截断的未完 URL（端口/路径在下一
    chunk），推迟到下一轮再判定——这是「:80 / :812 半截端口」假条目的根因
    （2026-09-27 实测：http://127.0.0.1:8123 被拆成 127.0.0.1 与 :812 两条）。
    流结束时调用方需用 scan_text_for_local_urls 对残余 buffer 收尾（flush）。
    """
    urls: List[str] = []
    for match in _LOCAL_URL_RE.finditer(buffer):
        if match.end() >= len(buffer):
            continue  # 触及末尾，可能未完，推迟
        url = _accept_url(match.group(0))
        if url and url not in seen:
            urls.append(url)
    return urls


def _target_key(target: Dict[str, Any]) -> str:
    if target.get("type") == "server":
        return f"server:{target.get('url', '')}"
    return f"file:{target.get('path', '')}"


def _origin(url: str) -> str:
    """URL 的 origin（协议+主机+端口），同 origin 不同 path 视为同一服务器。"""
    match = re.match(r"(https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?)", url, re.IGNORECASE)
    return match.group(1) if match else url


def _server_label(url: str) -> str:
    """服务器标签：:端口[/路径]。同 origin 保留多条后，
    路径必须进 label，否则同端口的根地址与具体页面在列表里无法区分。"""
    port_match = re.search(r":(\d{1,5})(?:/|$)", url)
    port = port_match.group(1) if port_match else "80"
    path_match = re.match(r"https?://[^/]+(/[^\s]*)?", url, re.IGNORECASE)
    path = ""
    if path_match and path_match.group(1) and path_match.group(1) != "/":
        path = path_match.group(1)
        if len(path) > 24:
            path = path[:23] + "…"
    return f":{port}{path}"


def _normalize(entries: Any) -> List[Dict[str, Any]]:
    """加载时清洗 + 去重（所有读取出口统一走这里，老对话读出自愈）。

    服务器条目按 2026-09-29 新规则重新过一遍 `_accept_url`：脏 URL
    （Markdown/中文粘连）截回干净形态重新去重，裸主机/噪音条目直接剔除——
    旧对话的存量垃圾无需迁移脚本即自动消失。
    """
    if not isinstance(entries, list):
        return []
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "server":
            raw_url = str(item.get("url") or "")
            # 脏 URL 可能粘着非 ASCII 尾巴，用新正则重新提取干净前缀
            clean = None
            rematch = _LOCAL_URL_RE.search(raw_url)
            if rematch:
                clean = _accept_url(rematch.group(0))
            if not clean:
                continue  # 裸主机/噪音/不可修复的脏条目，剔除
            # 无论 URL 是否变化都重算 origin/label：旧条目可能带着已删除的
            # 框架猜测后缀（「· HTTP Server」），统一为新格式
            item = dict(item)
            item["url"] = clean
            item["origin"] = _origin(clean)
            item["label"] = _server_label(clean)
        key = _target_key(item)
        if key in seen or key.endswith(":"):
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def record_file_target(context_manager: Any, conversation_id: Optional[str], rel_path: str) -> None:
    """记录 HTML 文件预览目标（由 _record_edited_file 挂钩调用）。"""
    if not is_preview_enabled():
        return
    if not is_previewable_file(rel_path):
        return
    rel = str(rel_path).strip().replace("\\", "/")
    label = rel.rsplit("/", 1)[-1]
    _mutate(context_manager, conversation_id, {
        "type": "file", "path": rel, "label": label, "source": "file_edit",
    })


def record_url_targets(
    context_manager: Any,
    conversation_id: Optional[str],
    urls: List[str],
    *,
    source: str,
) -> None:
    """记录服务器 URL 预览目标（同完整 URL 去重，同 origin 不同 path 保留多条）。"""
    if not is_preview_enabled():
        return
    for url in urls:
        _mutate(context_manager, conversation_id, {
            "type": "server", "url": url, "origin": _origin(url),
            "label": _server_label(url), "source": source,
        })


def purge_partial_stream_servers(context_manager: Any, conversation_id: Optional[str], full_urls: List[str]) -> None:
    """自愈清除：完整 URL 到达时，移除流式扫描「前缀截断」产生的假服务器条目。

    判定（2026-09-27 修正）：用已有条目的**完整 URL** 做真前缀匹配，且要求截断
    点在 token 中间（下一字符是字母/数字/冒号，如 :812→:8123、裸主机→:端口）；
    截在路径边界（/ ? #）的是合法独立条目——同 origin 保留多条后，旧逻辑用
    origin（不带路径）匹配会把同 origin 的合法条目（/、/menu）全误删。
    仅清除 model_output 来源（流式扫描）的条目。
    """
    if not full_urls:
        return

    def _is_truncation_prefix(stored_url: str, new_url: str) -> bool:
        if not stored_url or stored_url == new_url or not new_url.startswith(stored_url):
            return False
        if stored_url.endswith("/"):
            return False  # 截在路径边界，是合法条目
        next_char = new_url[len(stored_url)]
        return next_char.isalnum() or next_char == ":"

    def _do(entries: List[Dict[str, Any]]) -> bool:
        before = len(entries)
        kept: List[Dict[str, Any]] = []
        for item in entries:
            stored_url = str(item.get("url") or "")
            drop = (
                item.get("type") == "server"
                and item.get("source") == "model_output"
                and stored_url
                and any(_is_truncation_prefix(stored_url, u) for u in full_urls)
            )
            if not drop:
                kept.append(item)
        entries[:] = kept
        return len(entries) != before

    _mutate_raw(context_manager, conversation_id, _do)


def remove_target(context_manager: Any, conversation_id: Optional[str], key: str) -> None:
    """删除指定 key 的预览目标（key 格式：server:<url> 或 file:<path>）。"""

    def _do(entries: List[Dict[str, Any]]) -> bool:
        before = len(entries)
        entries[:] = [e for e in entries if _target_key(e) != key]
        return len(entries) != before

    _mutate_raw(context_manager, conversation_id, _do)


def _mutate(context_manager: Any, conversation_id: Optional[str], target: Dict[str, Any]) -> None:
    def _do(entries: List[Dict[str, Any]]) -> bool:
        key = _target_key(target)
        now = datetime.now().isoformat()
        # 服务器按完整 URL 去重：同 origin 的不同 path 保留多条（用户拍板
        # 2026-09-27——根地址与具体页面是两个独立预览目标）；同 URL 的重复命中
        # 不写不广播（流式扫描会反复命中同一 URL，每次都写+广播会造成事件刷屏）
        if target.get("type") == "server":
            for item in entries:
                if item.get("type") == "server" and item.get("url") == target.get("url"):
                    return False
        else:
            for item in entries:
                if _target_key(item) == key:
                    # 同一文件反复编辑：目标已存在即可，不重复写/广播
                    return False
        if len(entries) >= MAX_PREVIEW_TARGETS:
            return False
        new_item = dict(target)
        new_item["ts"] = now
        entries.append(new_item)
        return True

    _mutate_raw(context_manager, conversation_id, _do)


def _mutate_raw(context_manager: Any, conversation_id: Optional[str], mutator) -> None:
    """读取-修改-写回 metadata.preview_targets 并广播（与 edited_files 同链路）。"""
    try:
        if context_manager is None:
            return
        conv_id = conversation_id or getattr(context_manager, "current_conversation_id", None)
        if not conv_id:
            return
        router = getattr(context_manager, "_get_conversation_manager_for_id", None)
        manager = None
        if callable(router):
            try:
                manager = router(conv_id)
            except Exception:
                manager = None
        if manager is None:
            manager = getattr(context_manager, "conversation_manager", None)
        if manager is None:
            return
        data = manager.load_conversation(conv_id)
        if not data:
            return
        metadata = data.get("metadata", {}) or {}
        entries = _normalize(metadata.get("preview_targets"))
        if not mutator(entries):
            return
        if not manager.update_conversation_metadata(conv_id, {"preview_targets": entries}):
            return
        callback = getattr(context_manager, "_web_terminal_callback", None)
        if callable(callback):
            try:
                callback("preview_targets_updated", {
                    "conversation_id": conv_id,
                    "preview_targets": entries,
                })
            except Exception:
                pass
    except Exception:
        # 预览检测是辅助功能，任何失败都不能影响主链路
        pass
