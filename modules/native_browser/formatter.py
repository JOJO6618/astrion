"""浏览器工具结果 → 模型上下文文本。"""
from __future__ import annotations

import json
from typing import Any, Dict, List


def _tab_line(tab: Dict[str, Any]) -> str:
    title = str(tab.get("title") or "").strip() or "(无标题)"
    return f"[标签 {tab.get('id')}] {title} — {tab.get('url') or 'about:blank'}"


def _snapshot_block(snapshot: Dict[str, Any]) -> List[str]:
    kind = snapshot.get("kind")
    text = str(snapshot.get("text") or "")
    if kind == "none":
        return ["页面无变化。"]
    if kind == "diff":
        header = "页面变化（相对你上次看到的快照，可能含用户的操作）："
    else:
        header = "页面快照（局部）：" if snapshot.get("partial") else "页面快照（完整）："
    lines = [header, text]
    if snapshot.get("truncated"):
        lines.append(
            f"[快照已截断：显示 {len(text)} / 共 {snapshot.get('total')} 字符。"
            "用 browser_find 查找内容，或用 browser_snapshot 的 target/depth 查看局部]"
        )
    return lines


def format_for_model(result: Dict[str, Any]) -> str:
    lines: List[str] = []
    if result.get("success") is False:
        lines.append(f"失败：{result.get('error') or result.get('message') or '未知错误'}")
        dialog = result.get("dialog")
        if isinstance(dialog, dict):
            lines.append(f"页面对话框（{dialog.get('type')}）：{dialog.get('message')}")
        if isinstance(result.get("tab"), dict):
            lines.append(_tab_line(result["tab"]))
        return "\n".join(lines)
    op = result.get("op")
    if op == "tabs" and isinstance(result.get("tabs"), list):
        tabs = result["tabs"]
        if not tabs:
            return "本对话还没有浏览器标签。"
        return "本对话的标签：\n" + "\n".join(
            f"- {t.get('id')}{' [当前]' if t.get('current') else ''} {str(t.get('title') or '').strip() or '(无标题)'} — {t.get('url')}"
            for t in tabs
        )
    if isinstance(result.get("tab"), dict):
        lines.append(_tab_line(result["tab"]))
    if result.get("message"):
        lines.append(str(result["message"]))
    for note in result.get("notes") or []:
        lines.append(str(note))
    for item in result.get("downloads") or []:
        lines.append(f"已开始下载：{item.get('name')} → {item.get('path')}")
    dialog = result.get("dialog")
    if isinstance(dialog, dict):
        extra = f"（默认输入：{dialog.get('defaultPrompt')}）" if dialog.get("defaultPrompt") else ""
        lines.append(f"页面对话框（{dialog.get('type')}）：{dialog.get('message')}{extra}")
    if op == "find":
        matches = result.get("matches") or []
        total = result.get("total") or 0
        if not matches:
            lines.append("没有找到匹配内容。")
        else:
            lines.append(f"找到 {total} 处{'（显示前 30 处）' if total > len(matches) else ''}：")
            for m in matches:
                lines.append(f"- {m.get('line')}" + (f"  ⟵ 位于 {m.get('parent')}" if m.get("parent") else ""))
    if op == "evaluate":
        lines.append("返回值：")
        lines.append(str(result.get("result")))
    if op == "console_messages":
        messages = result.get("messages") or []
        if not messages:
            lines.append("没有控制台消息。")
        for m in messages:
            source = f"  ({m.get('source')})" if m.get("source") else ""
            lines.append(f"[{m.get('level')}] {m.get('message')}{source}")
    if op == "network_requests":
        requests = result.get("requests") or []
        if not requests:
            lines.append("没有记录到网络请求。")
        for r in requests:
            status = r.get("failed") or (r.get("status") if r.get("done") or r.get("status") else "进行中")
            lines.append(f"#{r.get('id')} {r.get('method')} {status} {r.get('type')} {r.get('url')}")
    if op == "network_request" and isinstance(result.get("request"), dict):
        r = result["request"]
        lines.append(f"#{r.get('seq')} {r.get('method')} {r.get('url')}")
        lines.append(f"类型 {r.get('type')}，状态 {r.get('failed') or r.get('status')} {r.get('statusText') or ''}，{r.get('mimeType') or ''}".strip())
        lines.append("请求头：" + json.dumps(r.get("requestHeaders") or {}, ensure_ascii=False))
        if r.get("postData"):
            lines.append("请求体：" + str(r["postData"]))
        lines.append("响应头：" + json.dumps(r.get("responseHeaders") or {}, ensure_ascii=False))
        if r.get("bodyNote"):
            lines.append(f"响应体（{r['bodyNote']}）：" if r.get("body") else f"响应体：{r['bodyNote']}")
        if r.get("body"):
            if not r.get("bodyNote"):
                lines.append("响应体：")
            lines.append(str(r["body"]))
    if op == "take_screenshot" and result.get("screenshot_path"):
        attached = "，图片已附在本结果中" if result.get("image_attached") else "（当前模型不支持图片输入，未附加图片）"
        lines.append(f"截图已保存：{result['screenshot_path']}{attached}")
    if isinstance(result.get("snapshot"), dict):
        lines.extend(_snapshot_block(result["snapshot"]))
    return "\n".join(line for line in lines if line is not None)
