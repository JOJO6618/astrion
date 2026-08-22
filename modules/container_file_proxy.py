"""Utilities to proxy FileManager operations into user containers."""

from __future__ import annotations

import json
import subprocess
import shutil
from pathlib import Path
from typing import Dict, Optional, Any, TYPE_CHECKING

CONTAINER_FILE_SCRIPT = r"""
import json
import sys
import pathlib
import shutil

def _resolve(root: pathlib.Path, rel: str) -> pathlib.Path:
    base = root.resolve()
    target = (base / rel).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError("路径越界: %s" % rel)
    return target

def _read_text(target: pathlib.Path):
    with target.open('r', encoding='utf-8') as fh:
        data = fh.read()
    lines = data.splitlines(keepends=True)
    return data, lines

def _ensure_file(target: pathlib.Path):
    if not target.exists():
        return {"success": False, "error": "文件不存在"}
    if not target.is_file():
        return {"success": False, "error": "不是文件"}
    return None

def _create_file(root, payload):
    rel = payload.get("path")
    target = _resolve(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = payload.get("content") or ""
    with target.open('w', encoding='utf-8') as fh:
        fh.write(content)
    return {"success": True, "path": rel, "size": len(content)}

def _delete_file(root, payload):
    rel = payload.get("path")
    target = _resolve(root, rel)
    err = _ensure_file(target)
    if err:
        return err
    target.unlink()
    return {"success": True, "path": rel, "action": "deleted"}

def _rename_file(root, payload):
    old_rel = payload.get("old_path")
    new_rel = payload.get("new_path")
    old = _resolve(root, old_rel)
    new = _resolve(root, new_rel)
    if not old.exists():
        return {"success": False, "error": "原文件不存在"}
    if new.exists():
        return {"success": False, "error": "目标文件已存在"}
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    return {
        "success": True,
        "old_path": old_rel,
        "new_path": new_rel,
        "action": "renamed"
    }

def _create_folder(root, payload):
    rel = payload.get("path")
    target = _resolve(root, rel)
    if target.exists():
        return {"success": False, "error": "文件夹已存在"}
    target.mkdir(parents=True, exist_ok=True)
    return {"success": True, "path": rel}

def _delete_folder(root, payload):
    rel = payload.get("path")
    target = _resolve(root, rel)
    if not target.exists():
        return {"success": False, "error": "文件夹不存在"}
    if not target.is_dir():
        return {"success": False, "error": "不是文件夹"}
    shutil.rmtree(target)
    return {"success": True, "path": rel}

def _read_file(root, payload):
    rel = payload.get("path")
    limit = payload.get("size_limit")
    target = _resolve(root, rel)
    err = _ensure_file(target)
    if err:
        return err
    size = target.stat().st_size
    if limit and size > limit:
        return {
            "success": False,
            "error": f"文件太大 ({size} 字节)，超过限制"
        }
    with target.open('r', encoding='utf-8') as fh:
        content = fh.read()
    return {"success": True, "path": rel, "content": content, "size": size}

def _read_text_segment(root, payload):
    rel = payload.get("path")
    start = payload.get("start_line")
    end = payload.get("end_line")
    limit = payload.get("size_limit")
    target = _resolve(root, rel)
    err = _ensure_file(target)
    if err:
        return err
    size = target.stat().st_size
    if limit and size > limit:
        return {
            "success": False,
            "error": f"文件太大 ({size} 字节)，超过限制"
        }
    data, lines = _read_text(target)
    total = len(lines)
    if total == 0:
        return {
            "success": True,
            "path": rel,
            "content": "",
            "size": size,
            "line_start": 0,
            "line_end": 0,
            "total_lines": 0
        }
    line_start = start if start and start > 0 else 1
    line_end = end if end and end >= line_start else total
    if line_start > total:
        return {"success": False, "error": "起始行超出文件长度"}
    line_end = min(line_end, total)
    snippet = "".join(lines[line_start - 1 : line_end])
    return {
        "success": True,
        "path": rel,
        "content": snippet,
        "size": size,
        "line_start": line_start,
        "line_end": line_end,
        "total_lines": total
    }

def _search_text(root, payload):
    rel = payload.get("path")
    target = _resolve(root, rel)
    err = _ensure_file(target)
    if err:
        return err
    data, lines = _read_text(target)
    total = len(lines)
    query = payload.get("query") or ""
    if not query:
        return {"success": False, "error": "缺少搜索关键词"}
    max_matches = payload.get("max_matches") or 10
    before = payload.get("context_before") or 2
    after = payload.get("context_after") or 2
    case_sensitive = bool(payload.get("case_sensitive"))
    query_cmp = query if case_sensitive else query.lower()

    def contains(text):
        text_cmp = text if case_sensitive else text.lower()
        return query_cmp in text_cmp

    matches = []
    for idx, line in enumerate(lines, start=1):
        if contains(line):
            win_start = max(1, idx - before)
            win_end = min(total, idx + after)
            if matches and win_start <= matches[-1]["line_end"]:
                matches[-1]["line_end"] = max(matches[-1]["line_end"], win_end)
                matches[-1]["hits"].append(idx)
            else:
                if len(matches) >= max_matches:
                    break
                matches.append({
                    "line_start": win_start,
                    "line_end": win_end,
                    "hits": [idx]
                })
    for window in matches:
        snippet_lines = lines[window["line_start"] - 1 : window["line_end"]]
        window["snippet"] = "".join(snippet_lines)

    return {
        "success": True,
        "path": rel,
        "size": target.stat().st_size,
        "total_lines": total,
        "matches": matches
    }

def _extract_segments(root, payload):
    rel = payload.get("path")
    target = _resolve(root, rel)
    err = _ensure_file(target)
    if err:
        return err
    segments = payload.get("segments") or []
    if not segments:
        return {"success": False, "error": "缺少要提取的行区间"}
    _, lines = _read_text(target)
    total = len(lines)
    extracted = []
    for spec in segments:
        start = spec.get("start_line")
        end = spec.get("end_line")
        label = spec.get("label")
        if start is None or end is None:
            return {"success": False, "error": "segments 中缺少 start_line 或 end_line"}
        if start <= 0 or end < start:
            return {"success": False, "error": "行区间不合法"}
        if start > total:
            return {"success": False, "error": f"区间起点 {start} 超出文件行数"}
        end = min(end, total)
        snippet = "".join(lines[start - 1 : end])
        extracted.append({
            "label": label,
            "line_start": start,
            "line_end": end,
            "content": snippet
        })
    return {
        "success": True,
        "path": rel,
        "size": target.stat().st_size,
        "total_lines": total,
        "segments": extracted
    }

def _write_file(root, payload):
    rel = payload.get("path")
    content = payload.get("content") or ""
    mode = payload.get("mode") or "w"
    target = _resolve(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open(mode, encoding='utf-8') as fh:
        fh.write(content)
    return {
        "success": True,
        "path": rel,
        "size": len(content),
        "mode": mode
    }

def _apply_modify_blocks(root, payload):
    rel = payload.get("path")
    blocks = payload.get("blocks") or []
    target = _resolve(root, rel)
    err = _ensure_file(target)
    if err:
        return err
    original, _ = _read_text(target)
    current = original
    results = []
    completed = []
    failed = []
    for block in blocks:
        idx = block.get("index")
        old_text = (block.get("old") or "").replace('\r\n', '\n')
        new_text = (block.get("new") or "").replace('\r\n', '\n')
        record = {
            "index": idx,
            "status": "pending",
            "removed_lines": 0,
            "added_lines": 0,
            "reason": None,
            "hint": None
        }
        if old_text is None or new_text is None:
            record.update({
                "status": "error",
                "reason": "缺少 OLD 或 NEW 内容",
                "hint": "请确认补丁是否完整。"
            })
            failed.append({"index": idx, "reason": "缺少 OLD/NEW"})
            results.append(record)
            continue
        if not old_text:
            record.update({
                "status": "error",
                "reason": "OLD 内容不能为空",
                "hint": "请确认要替换的原文是否准确复制。"
            })
            failed.append({"index": idx, "reason": "OLD 为空"})
            results.append(record)
            continue
        pos = current.find(old_text)
        if pos == -1:
            record.update({
                "status": "not_found",
                "reason": "未找到匹配的原文，请确认是否完全复制",
                "hint": "可使用终端或搜索确认原文。"
            })
            failed.append({"index": idx, "reason": "未找到匹配"})
            results.append(record)
            continue
        current = current[:pos] + new_text + current[pos + len(old_text):]
        removed_lines = old_text.count('\n')
        added_lines = new_text.count('\n')
        if old_text and not old_text.endswith('\n'):
            removed_lines += 1
        if new_text and not new_text.endswith('\n'):
            added_lines += 1
        record.update({
            "status": "success",
            "removed_lines": removed_lines,
            "added_lines": added_lines
        })
        completed.append(idx)
        results.append(record)
    write_performed = False
    error = None
    if completed:
        try:
            with target.open('w', encoding='utf-8') as fh:
                fh.write(current)
            write_performed = True
        except Exception as exc:
            error = f"写入文件失败: {exc}"
            try:
                with target.open('w', encoding='utf-8') as fh:
                    fh.write(original)
            except Exception:
                pass
    return {
        "success": bool(completed) and not failed and error is None,
        "completed": completed,
        "failed": failed,
        "results": results,
        "write_performed": write_performed,
        "error": error
    }

def _edit_lines(root, payload):
    rel = payload.get("path")
    start_line = int(payload.get("start_line") or 1)
    end_line = int(payload.get("end_line") or start_line)
    content = payload.get("content") or ""
    operation = payload.get("operation")
    target = _resolve(root, rel)
    err = _ensure_file(target)
    if err:
        return err
    if start_line < 1:
        return {"success": False, "error": "行号必须从1开始"}
    if end_line < start_line:
        return {"success": False, "error": "结束行号不能小于起始行号"}
    with target.open('r', encoding='utf-8') as fh:
        lines = fh.readlines()
    total = len(lines)
    if start_line > total:
        if operation == "insert":
            lines.extend([''] * (start_line - total - 1))
            lines.append(content if content.endswith('\n') else content + '\n')
            affected = len(content.splitlines() or [''])
        else:
            return {"success": False, "error": f"起始行号 {start_line} 超出文件范围 (共 {total} 行)"}
    else:
        if end_line > total:
            return {"success": False, "error": f"结束行号 {end_line} 超出文件范围 (共 {total} 行)"}
        start_idx = start_line - 1
        end_idx = end_line
        if operation == "replace":
            new_lines = content.split('\n') if '\n' in content else [content]
            formatted = []
            for i, line in enumerate(new_lines):
                if i < len(new_lines) - 1 or (end_idx < len(lines) and lines[end_idx - 1].endswith('\n')):
                    formatted.append(line + '\n' if not line.endswith('\n') else line)
                else:
                    formatted.append(line)
            lines[start_idx:end_idx] = formatted
            affected = end_line - start_line + 1
        elif operation == "insert":
            new_lines = content.split('\n') if '\n' in content else [content]
            formatted = [line + '\n' if not line.endswith('\n') else line for line in new_lines]
            lines[start_idx:start_idx] = formatted
            affected = len(formatted)
        elif operation == "delete":
            del lines[start_idx:end_idx]
            affected = end_line - start_line + 1
        else:
            return {"success": False, "error": f"未知的操作类型: {operation}"}
    with target.open('w', encoding='utf-8') as fh:
        fh.writelines(lines)
    return {
        "success": True,
        "path": rel,
        "operation": operation,
        "affected_lines": affected
    }

HANDLERS = {
    "create_file": _create_file,
    "delete_file": _delete_file,
    "rename_file": _rename_file,
    "create_folder": _create_folder,
    "delete_folder": _delete_folder,
    "read_file": _read_file,
    "read_text_segment": _read_text_segment,
    "search_text": _search_text,
    "extract_segments": _extract_segments,
    "write_file": _write_file,
    "apply_modify_blocks": _apply_modify_blocks,
    "edit_lines_range": _edit_lines,
}

def main():
    raw = sys.stdin.read()
    if not raw:
        raise RuntimeError("空请求")
    request = json.loads(raw)
    root = pathlib.Path(request["root"])
    action = request["action"]
    payload = request.get("payload") or {}
    handler = HANDLERS.get(action)
    if not handler:
        raise RuntimeError(f"未知操作: {action}")
    result = handler(root, payload)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stdout.write(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
"""

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle


class ContainerFileProxy:
    """Execute file operations inside a Docker container."""

    def __init__(self, container_session: "ContainerHandle"):
        self.container_session = container_session

    def is_available(self) -> bool:
        return bool(
            self.container_session
            and self.container_session.mode == "docker"
            and self.container_session.container_name
        )

    def update_session(self, session: Optional["ContainerHandle"]):
        self.container_session = session

    def run(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_available():
            return {"success": False, "error": "容器未就绪，无法执行文件操作"}

        session = self.container_session
        docker_bin = session.sandbox_bin or shutil.which("docker")
        if not docker_bin:
            return {"success": False, "error": "未找到 Docker 运行时"}

        request = {
            "action": action,
            "root": session.mount_path or "/workspace",
            "payload": payload,
        }

        cmd = [docker_bin, "exec", "-i"]
        if session.mount_path:
            cmd.extend(["-w", session.mount_path])
        cmd.append(session.container_name)
        cmd.extend(["python3", "-c", CONTAINER_FILE_SCRIPT])

        try:
            completed = subprocess.run(
                cmd,
                input=json.dumps(request, ensure_ascii=False),
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"success": False, "error": f"容器执行失败: {exc}"}

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            message = stderr or stdout or "未知错误"
            return {"success": False, "error": f"容器返回错误: {message}"}

        output = completed.stdout or ""
        output = output.strip()
        if not output:
            return {"success": False, "error": "容器未返回任何结果"}
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {
                "success": False,
                "error": f"容器响应无法解析: {output[:200]}",
            }
