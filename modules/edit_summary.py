"""本次工作编辑摘要（Edit Summary）。

write_file / edit_file 成功后由工具执行层调用 `update_edit_summary`：
以「该文件本轮工作第一次被编辑前的内容（baseline）」为基线，与当前内容
做合并 diff（净变化口径），把统计与带行号上下文的 diff 行写入当前工作
user 消息的 metadata.edit_summary，并实时广播给前端渲染卡片。

设计要点：
- 同一文件多次编辑：baseline 只在首次记录时写入，之后每次编辑重算合并
  结果并整体覆盖——metadata 与前端显示始终是最后一次编辑后的最终状态。
- 工作中每次编辑都会持久化（auto_save force）并广播，任务异常停止时
  卡片保留最后一刻的状态，刷新后照常显示。
- metadata 不会进入模型上下文（api_client 发送前有字段白名单清洗）。
"""

from __future__ import annotations

import difflib
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

EDIT_SUMMARY_VERSION = 1

# 单文件 diff 行数上限（超出截断并标记 truncated）。
# write_file 内容上限约 100KB，8000 行足以覆盖正常文件全量 diff。
MAX_DIFF_LINES = 8000
# baseline 内容存储上限（字符数）；超过则不持久化 baseline，退化为
# 「仅当次编辑 diff」，避免超大文件把对话 JSON 撑爆。
MAX_BASELINE_CHARS = 400_000
# 每个变更块两端保留的上下文行数
CONTEXT_LINES = 3

WebCallback = Optional[Callable[[str, Dict[str, Any]], None]]


def _split_lines(text: Optional[str]) -> List[str]:
    if not text:
        return []
    return str(text).splitlines()


def compute_file_diff(
    baseline_text: Optional[str],
    current_text: Optional[str],
) -> Dict[str, Any]:
    """计算 baseline → current 的合并 diff。

    返回 {added, removed, lines, truncated}；lines 元素：
      {"type": "context", "content", "old_no", "new_no"}
      {"type": "add", "content", "new_no"}
      {"type": "remove", "content", "old_no"}
      {"type": "sep"}   —— 变更块（hunk）之间的分隔
    """
    result: Dict[str, Any] = {"added": 0, "removed": 0, "lines": [], "truncated": False}
    old_lines = _split_lines(baseline_text)
    new_lines = _split_lines(current_text)
    if old_lines == new_lines:
        return result

    try:
        opcodes = difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes()
    except Exception:
        # 兜底：不给明细，只给整文件行数计数
        result["added"] = len(new_lines)
        result["removed"] = len(old_lines)
        result["truncated"] = True
        return result

    # 1) 统计增删 + 收集变更段（两端各扩 CONTEXT_LINES 上下文），相邻段合并
    ranges: List[List[int]] = []  # [old_lo, old_hi, new_lo, new_hi]
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue
        if tag in ("replace", "delete"):
            result["removed"] += i2 - i1
        if tag in ("replace", "insert"):
            result["added"] += j2 - j1
        r = [
            max(0, i1 - CONTEXT_LINES),
            min(len(old_lines), i2 + CONTEXT_LINES),
            max(0, j1 - CONTEXT_LINES),
            min(len(new_lines), j2 + CONTEXT_LINES),
        ]
        if ranges and r[0] <= ranges[-1][1] and r[2] <= ranges[-1][3]:
            ranges[-1][1] = max(ranges[-1][1], r[1])
            ranges[-1][3] = max(ranges[-1][3], r[3])
        else:
            ranges.append(r)

    # 2) 逐段生成带行号的 diff 行（段内对子序列再跑一次 diff 保证对齐）
    lines: List[Dict[str, Any]] = []
    truncated = False
    for range_index, (o_lo, o_hi, n_lo, n_hi) in enumerate(ranges):
        if range_index > 0:
            lines.append({"type": "sep"})
        sub_old = old_lines[o_lo:o_hi]
        sub_new = new_lines[n_lo:n_hi]
        try:
            sub_opcodes = difflib.SequenceMatcher(None, sub_old, sub_new).get_opcodes()
        except Exception:
            sub_opcodes = [("replace", 0, len(sub_old), 0, len(sub_new))]
        old_no = o_lo + 1
        new_no = n_lo + 1
        for tag, i1, i2, j1, j2 in sub_opcodes:
            if tag == "equal":
                for k in range(i1, i2):
                    lines.append({
                        "type": "context",
                        "content": sub_old[k],
                        "old_no": old_no,
                        "new_no": new_no,
                    })
                    old_no += 1
                    new_no += 1
            else:
                if tag in ("replace", "delete"):
                    for k in range(i1, i2):
                        lines.append({"type": "remove", "content": sub_old[k], "old_no": old_no})
                        old_no += 1
                if tag in ("replace", "insert"):
                    for k in range(j1, j2):
                        lines.append({"type": "add", "content": sub_new[k], "new_no": new_no})
                        new_no += 1
            if len(lines) >= MAX_DIFF_LINES:
                truncated = True
                break
        if truncated:
            break

    result["lines"] = lines[:MAX_DIFF_LINES]
    result["truncated"] = truncated
    return result


def _find_current_work_user_message(context_manager: Any) -> Optional[Dict[str, Any]]:
    """定位当前这轮工作归属的 user 消息（与 work_timer / 浅版本控制同锚）。"""
    history = getattr(context_manager, "conversation_history", None) or []
    # 1) 精确：当前工作 user 消息 id（与浅版本控制共用同一归属字段）
    message_id = getattr(context_manager, "current_shallow_message_id", None)
    if message_id:
        for msg in reversed(history):
            if (
                isinstance(msg, dict)
                and msg.get("role") == "user"
                and msg.get("message_id") == message_id
            ):
                return msg
    # 2) 兜底：最后一条仍在 working 的 user 消息（工具执行期间必然 working）
    for msg in reversed(history):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        timer = (msg.get("metadata") or {}).get("work_timer")
        if isinstance(timer, dict) and timer.get("status") == "working":
            return msg
    # 3) 再兜底：最后一条 starts_work 的 user 消息
    for msg in reversed(history):
        if (
            isinstance(msg, dict)
            and msg.get("role") == "user"
            and (msg.get("metadata") or {}).get("starts_work") is True
        ):
            return msg
    return None


def _strip_fulltext_for_broadcast(summary: Dict[str, Any]) -> Dict[str, Any]:
    """广播副本剥离 current_text（前端只用 lines/计数，且全文可能很大）。

    持久化的对话 JSON 保留 current_text，供修改留痕（modify_history）重建 diff。
    """
    files = summary.get("files")
    if not isinstance(files, list):
        return summary
    stripped_files: List[Dict[str, Any]] = []
    changed = False
    for entry in files:
        if isinstance(entry, dict) and "current_text" in entry:
            entry = {k: v for k, v in entry.items() if k != "current_text"}
            changed = True
        stripped_files.append(entry)
    if not changed:
        return summary
    out = dict(summary)
    out["files"] = stripped_files
    return out


def _persist_and_broadcast(
    context_manager: Any,
    conversation_id: str,
    msg: Dict[str, Any],
    summary: Dict[str, Any],
    web_callback: WebCallback,
) -> None:
    """持久化对话并广播最新编辑摘要；同步刷新修改留痕文件。"""
    # 实时修改留痕：每次摘要变化即重渲染本轮 diff 文件（编辑时刻内容，零磁盘依赖）
    try:
        from modules.modify_history import update_modify_history_for_task
        update_modify_history_for_task(context_manager, msg, summary)
    except Exception as exc:
        print(f"⚠️ 修改留痕实时写入失败: {exc}")
    try:
        context_manager.auto_save_conversation(force=True)
    except Exception as exc:
        print(f"⚠️ 编辑摘要持久化失败: {exc}")
    if callable(web_callback):
        try:
            web_callback("edit_summary_updated", {
                "conversation_id": conversation_id,
                "message_id": msg.get("message_id"),
                "edit_summary": _strip_fulltext_for_broadcast(summary),
            })
        except Exception:
            pass


def update_edit_summary(
    context_manager: Any,
    *,
    path: Any,
    original_text: Optional[str],
    current_text: Optional[str],
    web_callback: WebCallback = None,
) -> None:
    """write_file / edit_file 成功后更新当前工作 user 消息的 edit_summary。

    original_text：本次编辑前的文件全文（新建文件为 None）。
    current_text：本次编辑后的文件全文。
    """
    try:
        rel_path = str(path or "").strip().replace("\\", "/")
        conversation_id = getattr(context_manager, "current_conversation_id", None)
        if not rel_path or not conversation_id:
            return
        msg = _find_current_work_user_message(context_manager)
        if msg is None:
            return

        metadata = msg.setdefault("metadata", {})
        summary = metadata.get("edit_summary")
        if not isinstance(summary, dict) or not isinstance(summary.get("files"), list):
            summary = {"version": EDIT_SUMMARY_VERSION, "updated_at": "", "files": []}
            metadata["edit_summary"] = summary

        entry = next(
            (
                item
                for item in summary["files"]
                if isinstance(item, dict) and item.get("path") == rel_path
            ),
            None,
        )
        if entry is None:
            # 首次记录：写入 baseline（本轮工作第一次编辑前的内容；None 表示新建文件）。
            # 此后 baseline 不再变化，保证多次编辑合并为净变化。
            baseline_too_large = isinstance(original_text, str) and len(original_text) > MAX_BASELINE_CHARS
            entry = {
                "path": rel_path,
                "baseline": None if baseline_too_large else original_text,
                "baseline_truncated": baseline_too_large,
                "created_at": datetime.now().isoformat(),
            }
            summary["files"].append(entry)

        # baseline 因超大被丢弃时无法用真实基线重算：退化为当次编辑 diff
        if entry.get("baseline_truncated") and isinstance(original_text, str):
            baseline_for_diff: Optional[str] = original_text
        else:
            baseline_for_diff = entry.get("baseline")

        diff = compute_file_diff(baseline_for_diff, current_text)
        # 新建文件（首次记录时文件不存在）全程保持 added；其余为 modified。
        # delete_file 走 remove_edit_summary_entry 移除记录，这里不会出现 deleted。
        status = "added" if entry.get("baseline") is None and not entry.get("baseline_truncated") else "modified"

        now_iso = datetime.now().isoformat()
        current_too_large = isinstance(current_text, str) and len(current_text) > MAX_BASELINE_CHARS
        entry.update({
            "status": status,
            "added": diff["added"],
            "removed": diff["removed"],
            "lines": diff["lines"],
            "truncated": bool(diff["truncated"]) or bool(entry.get("baseline_truncated")),
            # 编辑时刻的最新全文：供修改留痕重建 diff，不依赖任务结束时的磁盘状态
            "current_text": None if current_too_large else current_text,
            "current_truncated": current_too_large,
            "updated_at": now_iso,
        })
        summary["updated_at"] = now_iso

        _persist_and_broadcast(context_manager, conversation_id, msg, summary, web_callback)
    except Exception as exc:
        print(f"⚠️ 更新编辑摘要失败: {exc}")


def _mutate_summary_files(
    context_manager: Any,
    web_callback: WebCallback,
    mutator: Callable[[List[Dict[str, Any]]], bool],
) -> None:
    """读取-修改-写回当前工作 user 消息的 edit_summary.files。"""
    try:
        conversation_id = getattr(context_manager, "current_conversation_id", None)
        if not conversation_id:
            return
        msg = _find_current_work_user_message(context_manager)
        if msg is None:
            return
        metadata = msg.get("metadata") or {}
        summary = metadata.get("edit_summary")
        if not isinstance(summary, dict) or not isinstance(summary.get("files"), list):
            return
        if not mutator(summary["files"]):
            return
        summary["updated_at"] = datetime.now().isoformat()
        msg["metadata"] = metadata
        _persist_and_broadcast(context_manager, conversation_id, msg, summary, web_callback)
    except Exception as exc:
        print(f"⚠️ 更新编辑摘要失败: {exc}")


def remove_edit_summary_entry(
    context_manager: Any,
    *,
    path: Any,
    web_callback: WebCallback = None,
) -> None:
    """文件被删除后从编辑摘要中移除（与快捷窗口文件记录行为一致）。"""
    rel_path = str(path or "").strip().replace("\\", "/")
    if not rel_path:
        return

    def _remove(files: List[Dict[str, Any]]) -> bool:
        before = len(files)
        files[:] = [item for item in files if item.get("path") != rel_path]
        return len(files) != before

    _mutate_summary_files(context_manager, web_callback, _remove)


def rename_edit_summary_entry(
    context_manager: Any,
    *,
    old_path: Any,
    new_path: Any,
    web_callback: WebCallback = None,
) -> None:
    """文件重命名后同步更新编辑摘要中的路径（与快捷窗口文件记录行为一致）。"""
    old_rel = str(old_path or "").strip().replace("\\", "/")
    new_rel = str(new_path or "").strip().replace("\\", "/")
    if not old_rel or not new_rel or old_rel == new_rel:
        return

    def _rename(files: List[Dict[str, Any]]) -> bool:
        for item in files:
            if item.get("path") == old_rel:
                item["path"] = new_rel
                item["updated_at"] = datetime.now().isoformat()
                return True
        return False

    _mutate_summary_files(context_manager, web_callback, _rename)
