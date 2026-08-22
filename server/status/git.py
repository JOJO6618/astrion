from __future__ import annotations
from server.status import status_bp
import time
import re
import os
import json
import shutil
import subprocess
import sys
import tempfile
import plistlib
from pathlib import Path
from flask import Blueprint, jsonify, request, send_file, session

from server.auth_helpers import api_login_required, resolve_admin_policy
from server.context import with_terminal, attach_user_broadcast
from server.state import (
    PROJECT_STORAGE_CACHE,
    PROJECT_STORAGE_CACHE_TTL_SECONDS,
    PROJECT_MAX_STORAGE_MB,
    container_manager,
    user_manager,
)
from config import AGENT_VERSION, TERMINAL_SANDBOX_MODE
from modules.host_workspace_manager import (
    create_host_workspace,
    delete_host_workspace,
    load_host_workspace_catalog,
    rename_host_workspace,
    resolve_host_workspace,
    set_default_host_workspace,
)
from utils.host_workspace_debug import write_host_workspace_debug
from server.utils_common import log_conn_diag
import server.state as state
def _run_project_git(project_path: Path, args: list[str]) -> tuple[bool, str]:
    git_bin = shutil.which("git")
    if not git_bin:
        return False, ""
    try:
        proc = subprocess.run(
            [git_bin, "-c", "core.quotePath=false", "-C", str(project_path), *args],
            cwd=str(project_path),
            # git 输出固定为 UTF-8（core.quotePath=false 时中文路径亦为 UTF-8 字节）；
            # Windows text=True 默认按 GBK 解码会让 _readerthread 抛 UnicodeDecodeError，
            # 导致输出被截断、侧边栏拿不到数据，必须显式指定编码。
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except Exception:
        return False, ""
    return proc.returncode == 0, (proc.stdout or "").strip()

def _run_project_git_raw(project_path: Path, args: list[str], timeout: int = 4) -> tuple[bool, str]:
    git_bin = shutil.which("git")
    if not git_bin:
        return False, ""
    try:
        proc = subprocess.run(
            [git_bin, "-c", "core.quotePath=false", "-C", str(project_path), *args],
            cwd=str(project_path),
            # 同上：固定按 UTF-8 解码；git show 出的文件内容可能是 GBK 等旧编码，
            # errors=replace 保证个别坏字节不会炸掉整个读取线程。
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return False, ""
    return proc.returncode == 0, (proc.stdout or "")

def _sum_git_numstat(numstat_text: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for raw_line in (numstat_text or "").splitlines():
        parts = raw_line.split("\t", 2)
        if len(parts) < 2:
            continue
        if parts[0].isdigit():
            additions += int(parts[0])
        if parts[1].isdigit():
            deletions += int(parts[1])
    return additions, deletions

def _paths_from_git_numstat(numstat_text: str) -> list[str]:
    paths: list[str] = []
    for raw_line in (numstat_text or "").splitlines():
        parts = raw_line.split("\t")
        if len(parts) < 3:
            continue
        path = parts[-1].strip()
        if path:
            paths.append(path)
    return paths

def _count_text_lines(text: str) -> int:
    if text == "":
        return 0
    return len(text.splitlines())

def _git_file_old_line_count(repo_root: Path, rel_path: str) -> int:
    ok, text = _run_project_git_raw(repo_root, ["show", f"HEAD:{rel_path}"], timeout=3)
    if ok:
        return _count_text_lines(text)
    target = (repo_root / rel_path).resolve()
    try:
        if target.exists() and repo_root in target.parents:
            return _count_text_lines(target.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        pass
    return 0

def _git_file_lines(repo_root: Path, rel_path: str, ref: str = "HEAD") -> list[str]:
    ok, text = _run_project_git_raw(repo_root, ["show", f"{ref}:{rel_path}"], timeout=3)
    if ok:
        return text.splitlines()
    return []

def _working_file_lines(repo_root: Path, rel_path: str) -> list[str]:
    target = (repo_root / rel_path).resolve()
    try:
        if target.exists() and repo_root in target.parents:
            return target.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        pass
    return _git_file_lines(repo_root, rel_path)

def _parse_hunk_header(header: str) -> tuple[int, int, int, int]:
    match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))?", header)
    if not match:
        return 0, 0, 0, 0
    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    return old_start, old_count, new_start, new_count

def _parse_git_diff(repo_root: Path, diff_text: str, fold_context_map: dict[str, dict[str, int]] | None = None) -> list[dict]:
    files: list[dict] = []
    current: dict | None = None
    current_hunk: dict | None = None
    old_line = 0
    new_line = 0
    previous_old_end = 0
    previous_new_end = 0

    def finish_hunk():
        nonlocal current_hunk, current, previous_old_end, previous_new_end
        if not current_hunk or not current:
            current_hunk = None
            return
        path = current.get("path") or current.get("old_path") or ""
        old_start = current_hunk.get("old_start") or 0
        old_count = current_hunk.get("old_count") or 0
        new_start = current_hunk.get("new_start") or 0
        new_count = current_hunk.get("new_count") or 0
        current_hunk["before_hidden"] = max(0, int(old_start) - int(previous_old_end) - 1)
        before_fold_key = f"before:{old_start}"
        current_hunk["before_fold_key"] = before_fold_key
        requested_extra = int((fold_context_map or {}).get(path, {}).get(before_fold_key, 0) or 0)
        if requested_extra > 0 and current_hunk["before_hidden"] > 0:
            add_count = min(current_hunk["before_hidden"], requested_extra)
            old_lines = _git_file_lines(repo_root, current.get("old_path") or path)
            new_lines = _working_file_lines(repo_root, path)
            injected = []
            for idx in range(add_count):
                old_no = int(old_start) - add_count + idx
                new_no = int(new_start) - add_count + idx
                text = ""
                if 0 < new_no <= len(new_lines):
                    text = new_lines[new_no - 1]
                elif 0 < old_no <= len(old_lines):
                    text = old_lines[old_no - 1]
                injected.append({"kind": "context", "old_line": old_no, "new_line": new_no, "text": text})
            current_hunk["lines"] = injected + current_hunk.get("lines", [])
            current_hunk["before_hidden"] -= add_count
        previous_old_end = int(old_start) + int(old_count) - 1
        previous_new_end = int(new_start) + int(new_count) - 1
        current["hunks"].append(current_hunk)
        current_hunk = None

    def finish_file():
        nonlocal current, previous_old_end, previous_new_end
        finish_hunk()
        if not current:
            return
        old_total = _git_file_old_line_count(repo_root, current.get("old_path") or current.get("path") or "")
        current["after_hidden"] = max(0, old_total - int(previous_old_end))
        current["after_fold_key"] = "after"
        path = current.get("path") or current.get("old_path") or ""
        requested_extra = int((fold_context_map or {}).get(path, {}).get("after", 0) or 0)
        if requested_extra > 0 and current["after_hidden"] > 0 and current.get("hunks"):
            add_count = min(current["after_hidden"], requested_extra)
            old_lines = _git_file_lines(repo_root, current.get("old_path") or path)
            new_lines = _working_file_lines(repo_root, path)
            injected = []
            for idx in range(add_count):
                old_no = int(previous_old_end) + 1 + idx
                new_no = int(previous_new_end) + 1 + idx
                text = ""
                if 0 < new_no <= len(new_lines):
                    text = new_lines[new_no - 1]
                elif 0 < old_no <= len(old_lines):
                    text = old_lines[old_no - 1]
                injected.append({"kind": "context", "old_line": old_no, "new_line": new_no, "text": text})
            current["hunks"][-1]["lines"].extend(injected)
            current["after_hidden"] -= add_count
        files.append(current)
        current = None
        previous_old_end = 0
        previous_new_end = 0

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            finish_file()
            current = {
                "path": "",
                "old_path": "",
                "additions": 0,
                "deletions": 0,
                "hunks": [],
                "after_hidden": 0,
            }
            previous_old_end = 0
            continue
        if current is None:
            continue
        if raw.startswith("--- "):
            old_path = raw[4:].strip()
            current["old_path"] = "" if old_path == "/dev/null" else re.sub(r"^a/", "", old_path)
            continue
        if raw.startswith("+++ "):
            new_path = raw[4:].strip()
            current["path"] = current["old_path"] if new_path == "/dev/null" else re.sub(r"^b/", "", new_path)
            continue
        if raw.startswith("@@ "):
            finish_hunk()
            old_start, old_count, new_start, new_count = _parse_hunk_header(raw)
            current_hunk = {
                "header": raw,
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "before_hidden": 0,
                "lines": [],
            }
            old_line = old_start
            new_line = new_start
            continue
        if current_hunk is None:
            continue
        marker = raw[:1]
        if marker not in {" ", "+", "-"}:
            continue
        text = raw[1:]
        line = {"kind": "context", "old_line": old_line, "new_line": new_line, "text": text}
        if marker == "+":
            line = {"kind": "add", "old_line": None, "new_line": new_line, "text": text}
            new_line += 1
            current["additions"] += 1
        elif marker == "-":
            line = {"kind": "delete", "old_line": old_line, "new_line": None, "text": text}
            old_line += 1
            current["deletions"] += 1
        else:
            old_line += 1
            new_line += 1
        current_hunk["lines"].append(line)
    finish_file()
    return [item for item in files if item.get("path") and item.get("hunks")]

@status_bp.route('/api/project/git-summary')
@api_login_required
@with_terminal
def get_project_git_summary(terminal, workspace, username):
    """返回当前项目自身 git 仓库概览（不使用隐藏版本管理 git）。"""
    project_path = Path(getattr(workspace, "project_path", "") or "").expanduser().resolve()
    if not project_path.exists():
        return jsonify({"success": True, "data": {"has_git": False}})

    ok, root_text = _run_project_git(project_path, ["rev-parse", "--show-toplevel"])
    if not ok or not root_text:
        return jsonify({"success": True, "data": {"has_git": False}})

    repo_root = Path(root_text).expanduser().resolve()
    ok, branch = _run_project_git(repo_root, ["branch", "--show-current"])
    if not ok or not branch:
        ok, branch = _run_project_git(repo_root, ["rev-parse", "--short", "HEAD"])
        branch = f"HEAD {branch}" if ok and branch else "HEAD"
    _, upstream = _run_project_git(repo_root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])

    _, numstat_text = _run_project_git(repo_root, ["diff", "--numstat", "HEAD", "--"])
    additions, deletions = _sum_git_numstat(numstat_text)

    return jsonify({
        "success": True,
        "data": {
            "has_git": True,
            "project_name": repo_root.name,
            "project_path": str(repo_root),
            "branch": branch,
            "upstream": upstream,
            "additions": additions,
            "deletions": deletions,
        }
    })

@status_bp.route('/api/project/git-diff')
@api_login_required
@with_terminal
def get_project_git_diff(terminal, workspace, username):
    """返回当前项目自身 git 工作区 diff，用于前端侧边栏展示。"""
    project_path = Path(getattr(workspace, "project_path", "") or "").expanduser().resolve()
    if not project_path.exists():
        return jsonify({"success": True, "data": {"has_git": False, "files": []}})

    ok, root_text = _run_project_git(project_path, ["rev-parse", "--show-toplevel"])
    if not ok or not root_text:
        return jsonify({"success": True, "data": {"has_git": False, "files": []}})

    repo_root = Path(root_text).expanduser().resolve()
    try:
        context_lines = int(request.args.get("context", "3") or 3)
    except Exception:
        context_lines = 3
    context_lines = max(0, min(200, context_lines))
    context_map: dict[str, int] = {}
    raw_context_map = request.args.get("context_map", "").strip()
    if raw_context_map:
        try:
            parsed = json.loads(raw_context_map)
            if isinstance(parsed, dict):
                for key, value in parsed.items():
                    if not isinstance(key, str) or not key:
                        continue
                    try:
                        context_map[key] = max(0, min(200, int(value)))
                    except Exception:
                        continue
        except Exception:
            context_map = {}
    fold_context_map: dict[str, dict[str, int]] = {}
    raw_fold_context_map = request.args.get("fold_context_map", "").strip()
    if raw_fold_context_map:
        try:
            parsed = json.loads(raw_fold_context_map)
            if isinstance(parsed, dict):
                for path_key, folds in parsed.items():
                    if not isinstance(path_key, str) or not isinstance(folds, dict):
                        continue
                    safe_folds: dict[str, int] = {}
                    for fold_key, value in folds.items():
                        if not isinstance(fold_key, str) or not fold_key:
                            continue
                        try:
                            safe_folds[fold_key] = max(0, min(200, int(value)))
                        except Exception:
                            continue
                    if safe_folds:
                        fold_context_map[path_key] = safe_folds
        except Exception:
            fold_context_map = {}

    ok, branch = _run_project_git(repo_root, ["branch", "--show-current"])
    if not ok or not branch:
        ok, branch = _run_project_git(repo_root, ["rev-parse", "--short", "HEAD"])
        branch = f"HEAD {branch}" if ok and branch else "HEAD"
    _, upstream = _run_project_git(repo_root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    _, numstat_text = _run_project_git(repo_root, ["diff", "--numstat", "HEAD", "--"])
    additions, deletions = _sum_git_numstat(numstat_text)
    diff_text = ""
    if context_map:
        parts: list[str] = []
        for rel_path in _paths_from_git_numstat(numstat_text):
            per_file_context = context_map.get(rel_path, context_lines)
            ok, file_diff = _run_project_git_raw(
                repo_root,
                ["--no-pager", "diff", f"--unified={per_file_context}", "HEAD", "--", rel_path],
                timeout=8,
            )
            if ok and file_diff:
                parts.append(file_diff)
        diff_text = "\n".join(parts)
    else:
        ok, diff_text = _run_project_git_raw(repo_root, [f"--no-pager", "diff", f"--unified={context_lines}", "HEAD", "--"], timeout=8)
        if not ok:
            diff_text = ""

    return jsonify({
        "success": True,
        "data": {
            "has_git": True,
            "project_name": repo_root.name,
            "project_path": str(repo_root),
            "branch": branch,
            "upstream": upstream,
            "additions": additions,
            "deletions": deletions,
            "context": context_lines,
            "files": _parse_git_diff(repo_root, diff_text, fold_context_map=fold_context_map),
        }
    })
