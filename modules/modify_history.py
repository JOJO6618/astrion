"""修改留痕（Modify History）：把本轮工作的净修改实时落盘为增强版 unified diff。

设计要点（已与用户确认）：
- **实时写入**：与编辑摘要（edit_summary）完全同步——每次 write_file / edit_file /
  delete_file / rename_file 引起摘要变化时，立即重渲染本轮留痕文件
  （``update_modify_history_for_task``，挂在 ``modules/edit_summary._persist_and_broadcast``）。
  任务中途异常停止，留痕也保留到最后一次编辑的状态。
- **零磁盘依赖**：diff 的新侧内容来自 edit_summary entry 在编辑时刻记录的
  ``current_text``（全文，与 baseline 同享 400KB 上限），不读任务结束时的磁盘——
  ``run_command`` 对文件的改动/删除不会混入留痕（用户定义：只记录原生编辑工具）。
- **任务结束收尾**（``finalize_modify_history_for_task``，挂在
  ``server/chat_flow_task_main.finalize_user_work_timer``）：更新头部完成时间，
  并做文件存在性检查——任务结束时已不存在（被 rm/移动）的文件，小节转为
  ``/dev/null → 最后内容`` 的 new file diff 并标注，``git apply`` 可直接恢复该文件。
- 输出：``<工作区>/.astrion/modify_history/<conversation_id>/<用户输入截断>_<任务开始时间>.diff``
  —— 一次任务（一条用户输入）一个文件。
- 格式：``#`` 注释头（任务信息）+ 每文件小节注释 + 标准 ``diff --git`` 主体。
  注释行在 diff 块之外，不影响 ``git apply`` / ``patch``（已实测验证）：
  文件处于修改前状态时 ``git apply`` 重做，处于修改后状态时 ``git apply -R`` 撤销。
- 直接 IO 写入：绕开 write_file / edit_file 工具层，不触发深度备份
  （shallow_versioning.track_edit）与编辑摘要，零递归。
- 开关：个性化 ``modify_history_enabled``（默认开启）；关闭时既不落盘也不注入
  system prompt 附言。
- 已知边界：baseline 或 current 超 400KB 被截断的文件无法重建完整 diff，
  小节内会明确标注，仅保留计数。
"""

from __future__ import annotations

import difflib
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ASTRION_DIR_NAME = ".astrion"
MODIFY_HISTORY_DIR_NAME = "modify_history"
_PROMPT_FILE_NAME = "modify_history.txt"

_COMMENT_BAR_HEAVY = "# " + "═" * 60
_COMMENT_BAR_LIGHT = "# " + "─" * 60

# summary 中记录本轮留痕文件名的私有字段（随对话 JSON 持久化，供收尾定位同一文件）
_HISTORY_FILE_KEY = "_history_file"

# 文件名中用户输入片段的长度上限与文件系统非法字符
_FILENAME_INPUT_MAX_CHARS = 40
_FILENAME_ILLEGAL_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


# ---------------------------------------------------------------------------
# 路径与开关
# ---------------------------------------------------------------------------


def get_modify_history_dir(project_path: Any, conversation_id: Optional[str]) -> Optional[Path]:
    """本对话的留痕目录；conversation_id 缺失时返回 None。"""
    conv_id = str(conversation_id or "").strip()
    if not project_path or not conv_id:
        return None
    return (
        Path(project_path).expanduser().resolve()
        / _ASTRION_DIR_NAME
        / MODIFY_HISTORY_DIR_NAME
        / conv_id
    )


def is_modify_history_enabled(data_dir: Any) -> bool:
    """读取个性化开关；读取失败按默认开启处理。"""
    try:
        from modules.personalization_manager import load_personalization_config

        config = load_personalization_config(data_dir)
        return bool(config.get("modify_history_enabled", True))
    except Exception:
        return True


# ---------------------------------------------------------------------------
# system prompt 附言
# ---------------------------------------------------------------------------


def build_modify_history_prompt_note(
    *,
    project_path: Any,
    data_dir: Any,
    conversation_id: Optional[str],
) -> str:
    """构建注入 frozen system prompt 的留痕说明段落；开关关闭或信息缺失时返回空串。"""
    if not is_modify_history_enabled(data_dir):
        return ""
    history_dir = get_modify_history_dir(project_path, conversation_id)
    if history_dir is None:
        return ""
    try:
        prompt_path = Path(__file__).resolve().parent.parent / "prompts" / _PROMPT_FILE_NAME
        template = prompt_path.read_text(encoding="utf-8")
        return template.format(modify_history_dir=str(history_dir)).strip()
    except Exception as exc:
        logger.warning(f"[modify_history] 加载留痕 prompt 失败: {exc}")
        return ""


# ---------------------------------------------------------------------------
# diff 渲染
# ---------------------------------------------------------------------------


def _split_text_lines(text: Optional[str]) -> List[str]:
    if not text:
        return []
    return str(text).splitlines()


def _render_file_diff_body(path: str, status: str, old_text: Optional[str], new_text: str) -> List[str]:
    """单文件的标准 diff 主体（diff --git 头 + hunk），保证 git apply 可用。"""
    out: List[str] = [f"diff --git a/{path} b/{path}"]
    if status == "added":
        out.append("new file mode 100644")
        out.append("--- /dev/null")
    else:
        out.append(f"--- a/{path}")
    out.append(f"+++ b/{path}")
    old_lines = _split_text_lines(old_text)
    new_lines = _split_text_lines(new_text)
    diff_iter = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
        lineterm="",
    )
    for line in list(diff_iter)[2:]:  # 跳过 difflib 自产 ---/+++ 头，使用自拼头部
        out.append(line)
    return out


def render_modify_history_diff(
    *,
    conversation_id: str,
    user_message_text: str,
    started_at: Optional[str],
    finished_at: Optional[str],
    files: List[Dict[str, Any]],
) -> str:
    """渲染完整留痕文件内容。

    files 元素：
      path, status, added, removed, old_text, new_text,
      truncated            —— baseline/current 超上限，仅保留计数，不产 diff 主体
      missing_at_finalize  —— 收尾时文件已不存在：主体改为 /dev/null→new_text，可恢复
      unrecoverable        —— 文件已不存在且无内容记录：仅标注，不产 diff 主体
    """
    total_added = sum(int(f.get("added") or 0) for f in files)
    total_removed = sum(int(f.get("removed") or 0) for f in files)
    task_summary = " ".join(str(user_message_text or "").split())
    if len(task_summary) > 200:
        task_summary = task_summary[:200] + "…"

    lines: List[str] = [
        _COMMENT_BAR_HEAVY,
        "# 修改记录（Astrion Modify History）",
        _COMMENT_BAR_HEAVY,
        f"# 对话:     {conversation_id}",
        f"# 任务:     {task_summary}",
        f"# 时间:     {started_at or '?'} → {finished_at or '?'}",
        f"# 合计:     {len(files)} 个文件，+{total_added} / −{total_removed}",
        "#",
        "# 恢复方式:",
        "#   重做本次修改（文件处于修改前状态时）: git apply 本文件",
        "#   撤销本次修改（文件处于修改后状态时）: git apply -R 本文件",
        "#   恢复已删除文件（小节标注「已不存在」时）: git apply 本文件",
        "#   所有 # 注释行不影响 git apply / patch，无需剔除",
        "",
    ]

    for index, entry in enumerate(files, 1):
        path = str(entry.get("path") or "")
        status = str(entry.get("status") or "modified")
        status_label = "新建" if status == "added" else "修改"
        added = int(entry.get("added") or 0)
        removed = int(entry.get("removed") or 0)
        truncated = bool(entry.get("truncated"))
        missing = bool(entry.get("missing_at_finalize"))
        unrecoverable = bool(entry.get("unrecoverable"))
        lines.extend(
            [
                _COMMENT_BAR_LIGHT,
                f"# [{index}/{len(files)}] {path}",
                f"#       {status_label} · +{added} / −{removed}",
            ]
        )
        if truncated:
            lines.append("#       ⚠ 文件内容超出保存上限（400KB），无法重建完整 diff，仅保留计数")
        if missing and not unrecoverable:
            lines.append("#       ⚠ 文件在任务结束时已不存在（可能被删除/移动），以下按其最后记录内容完整保留，git apply 可直接恢复")
        if unrecoverable:
            lines.append("#       ⚠ 文件在任务结束时已不存在，且本轮无内容记录，无法重建")
        lines.append(_COMMENT_BAR_LIGHT)
        if truncated or unrecoverable:
            lines.append("")
            continue
        if missing:
            # 已消失文件：产出 new file diff（/dev/null → 最后内容），git apply 即恢复
            lines.extend(
                _render_file_diff_body(
                    path=path,
                    status="added",
                    old_text=None,
                    new_text=str(entry.get("new_text") or ""),
                )
            )
        else:
            lines.extend(
                _render_file_diff_body(
                    path=path,
                    status=status,
                    old_text=entry.get("old_text"),
                    new_text=str(entry.get("new_text") or ""),
                )
            )
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 任务信息辅助
# ---------------------------------------------------------------------------


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _task_started_at(msg: Dict[str, Any]) -> Optional[str]:
    metadata = msg.get("metadata") or {}
    timer = metadata.get("work_timer")
    if isinstance(timer, dict) and timer.get("started_at"):
        return str(timer.get("started_at"))
    return msg.get("timestamp") or None


def _message_text(msg: Dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # 多模态消息：拼接其中的文本片段
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return " ".join(parts)
    return str(content or "")


def _sanitize_input_for_filename(text: Any, max_chars: int = _FILENAME_INPUT_MAX_CHARS) -> str:
    """用户输入转文件名片段：压缩空白、非法字符替换为 _、截断；空结果回退 task。"""
    cleaned = " ".join(str(text or "").split())
    cleaned = _FILENAME_ILLEGAL_RE.sub("_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip(" ._")
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip(" ._")
    return cleaned or "task"


def _ensure_history_file_name(
    msg: Dict[str, Any],
    summary: Dict[str, Any],
    *,
    history_dir: Optional[Path] = None,
) -> str:
    """本轮留痕文件名：<用户输入截断>_<任务开始时间>.diff；首次生成后存入 summary 复用。

    同对话内输入（截断后）同名且同窗时间戳冲突时追加 _2/_3 序号，避免后轮覆盖前轮。
    """
    existing = summary.get(_HISTORY_FILE_KEY)
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    input_part = _sanitize_input_for_filename(_message_text(msg))
    dt = _parse_iso(_task_started_at(msg)) or datetime.now()
    base = f"{input_part}_{dt.strftime('%Y-%m-%d_%H%M%S_%f')[:-3]}"
    name = f"{base}.diff"
    if history_dir is not None:
        seq = 2
        while (history_dir / name).exists():
            name = f"{base}_{seq}.diff"
            seq += 1
    summary[_HISTORY_FILE_KEY] = name
    return name


def _build_files_payload(
    files: List[Any],
    *,
    project_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """把 edit_summary entries 转成渲染载荷；数据源为编辑时刻记录的 baseline/current。

    project_root 提供时做存在性检查（任务结束收尾）：已消失的文件按
    missing_at_finalize（有内容记录，可恢复）或 unrecoverable（无记录）处理。
    """
    payload: List[Dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        rel_path = str(entry.get("path") or "").strip()
        if not rel_path:
            continue
        current = entry.get("current_text")
        truncated = (
            bool(entry.get("truncated"))
            or bool(entry.get("baseline_truncated"))
            or bool(entry.get("current_truncated"))
        )
        item: Dict[str, Any] = {
            "path": rel_path,
            "status": str(entry.get("status") or "modified"),
            "added": entry.get("added") or 0,
            "removed": entry.get("removed") or 0,
            "old_text": entry.get("baseline"),
            "new_text": current if isinstance(current, str) else "",
            "truncated": truncated,
        }
        if project_root is not None:
            try:
                exists = (project_root / rel_path).is_file()
            except Exception:
                exists = True  # 检查本身失败时按存在处理，不误标
            if not exists:
                if isinstance(current, str) and not truncated:
                    item["missing_at_finalize"] = True
                else:
                    item["unrecoverable"] = True
        payload.append(item)
    return payload


def _write_history_file(history_dir: Path, file_name: str, content: str) -> str:
    history_dir.mkdir(parents=True, exist_ok=True)
    target = history_dir / file_name
    target.write_text(content, encoding="utf-8")
    return str(target)


# ---------------------------------------------------------------------------
# 实时写入（每次编辑后）与任务收尾（任务结束时）
# ---------------------------------------------------------------------------


def update_modify_history_for_task(
    context_manager: Any,
    msg: Dict[str, Any],
    summary: Dict[str, Any],
) -> Optional[str]:
    """编辑摘要每次变化时重渲染本轮留痕文件（实时）。

    由 modules/edit_summary._persist_and_broadcast 调用（覆盖 update/remove/rename
    三条路径）；数据源全部来自 summary entries（baseline + current_text），不读磁盘。
    返回留痕文件路径；开关关闭/信息缺失/无有效文件时返回 None。
    """
    try:
        if not isinstance(summary, dict):
            return None
        project_path = getattr(context_manager, "project_path", None)
        data_dir = getattr(context_manager, "data_dir", None)
        conversation_id = getattr(context_manager, "current_conversation_id", None)
        history_dir = get_modify_history_dir(project_path, conversation_id)
        if history_dir is None:
            return None

        files = summary.get("files")
        if not isinstance(files, list) or not files:
            # 本轮编辑已全部回滚（如 write 后又 delete_file）：移除已生成的留痕文件
            existing_name = summary.get(_HISTORY_FILE_KEY)
            if isinstance(existing_name, str) and existing_name.strip():
                if not is_modify_history_enabled(data_dir):
                    return None
                try:
                    (history_dir / existing_name.strip()).unlink(missing_ok=True)
                except Exception:
                    pass
                summary.pop(_HISTORY_FILE_KEY, None)
            return None

        if not is_modify_history_enabled(data_dir):
            return None

        file_name = _ensure_history_file_name(msg, summary, history_dir=history_dir)
        payload = _build_files_payload(files)
        if not payload:
            return None
        content = render_modify_history_diff(
            conversation_id=str(conversation_id),
            user_message_text=_message_text(msg),
            started_at=_task_started_at(msg),
            finished_at=datetime.now().isoformat(timespec="seconds"),
            files=payload,
        )
        return _write_history_file(history_dir, file_name, content)
    except Exception as exc:
        logger.warning(f"[modify_history] 实时写入修改留痕失败: {exc}")
        return None


def finalize_modify_history_for_task(
    *,
    project_path: Any,
    data_dir: Any,
    conversation_id: Optional[str],
    msg: Dict[str, Any],
    finished_at: Optional[str] = None,
) -> Optional[str]:
    """任务结束收尾：写入完成时间，并检查文件存在性。

    任务结束时已不存在（被 rm/移动）的文件，其小节转为 /dev/null→最后内容 的
    new file diff（git apply 可直接恢复）；无内容记录的标注为无法重建。
    由 server/chat_flow_task_main.finalize_user_work_timer 调用；直接 IO，失败仅告警。
    """
    try:
        metadata = msg.get("metadata") if isinstance(msg, dict) else None
        summary = (metadata or {}).get("edit_summary")
        if not isinstance(summary, dict):
            return None
        files = summary.get("files")
        if not isinstance(files, list) or not files:
            return None
        if not is_modify_history_enabled(data_dir):
            return None
        history_dir = get_modify_history_dir(project_path, conversation_id)
        if history_dir is None:
            return None
        project_root = Path(project_path).expanduser().resolve()

        file_name = summary.get(_HISTORY_FILE_KEY)
        if not isinstance(file_name, str) or not file_name.strip():
            # 实时路径未写过（异常/旧对话）：按收尾补一次完整渲染
            file_name = _ensure_history_file_name(msg, summary, history_dir=history_dir)

        payload = _build_files_payload(files, project_root=project_root)
        if not payload:
            return None
        content = render_modify_history_diff(
            conversation_id=str(conversation_id),
            user_message_text=_message_text(msg),
            started_at=_task_started_at(msg),
            finished_at=finished_at or datetime.now().isoformat(timespec="seconds"),
            files=payload,
        )
        return _write_history_file(history_dir, file_name.strip(), content)
    except Exception as exc:
        logger.warning(f"[modify_history] 收尾写入修改留痕失败: {exc}")
        return None
