"""Commit/branch comparisons always read immutable Git objects, never HEAD files."""
from __future__ import annotations

from pathlib import Path
import re

from .runner import GitQueryError, commit_sha, empty_tree, git, text

FIELDS = "%H%x00%P%x00%an%x00%aI%x00%s"


def _rows(raw: bytes) -> list[dict]:
    values = raw.decode("utf-8", "replace").split("\x00")
    if not raw:
        return []
    if values[-1] == "":
        values.pop()
    return [{"sha": values[i], "parents": values[i + 1].split(),
             "author": values[i + 2], "date": values[i + 3], "subject": values[i + 4]}
            for i in range(0, len(values) - 4, 5)]


def branches(repo: Path) -> dict:
    raw = text(repo, ["for-each-ref", "--sort=refname", "--format=%(refname)",
                      "refs/heads", "refs/remotes"])
    try:
        current = text(repo, ["symbolic-ref", "--short", "-q", "HEAD"]).strip()
    except GitQueryError:
        current = ""
    return {"branches": [{"ref": ref, "name": ref.removeprefix("refs/heads/")
                           .removeprefix("refs/remotes/")} for ref in raw.splitlines()
                          if not ref.endswith("/HEAD")], "current_branch": current}


def history(repo: Path, ref: str = "", search: str = "", offset: int = 0, limit: int = 40) -> dict:
    try:
        target = commit_sha(repo, ref or "HEAD")
    except GitQueryError:
        if not ref and not text(repo, ["for-each-ref", "--format=%(objectname)"]).strip():
            return {"commits": [], "has_more": False}
        raise
    args = ["log", "-z", f"--format={FIELDS}", f"--skip={offset}", f"--max-count={limit + 1}"]
    if search:
        args += ["--fixed-strings", "--regexp-ignore-case", f"--grep={search}"]
    args += [target, "--"]
    rows = _rows(git(repo, args))
    return {"commits": rows[:limit], "has_more": len(rows) > limit}


def comparison(repo: Path, *, sha: str = "", parent: str = "", base: str = "", head: str = "HEAD") -> dict:
    if sha:
        target = commit_sha(repo, sha)
        row = _rows(git(repo, ["show", "-s", "-z", f"--format={FIELDS}", target, "--"]))[0]
        parents = row["parents"]
        baseline = commit_sha(repo, parent) if parent else (parents[0] if parents else empty_tree(repo))
        if parent and baseline not in parents:
            raise GitQueryError("所选基准不是该提交的父提交。")
        row["message"] = text(repo, ["show", "-s", "--format=%B", target, "--"]).strip()
        return {"kind": "commit", "head_sha": target, "base_sha": baseline, "commit": row}
    if not base:
        raise GitQueryError("请选择基准分支。")
    target = commit_sha(repo, head)
    base_tip = commit_sha(repo, base)
    baseline = text(repo, ["merge-base", base_tip, target]).strip()
    if not baseline:
        raise GitQueryError("两个分支没有共同祖先。")
    return {"kind": "branch", "head_sha": target, "base_sha": baseline,
            "base_ref": base, "head_ref": head, "base_tip": base_tip}


def _diff_args(compare: dict) -> list[str]:
    return ["diff", "--no-ext-diff", "--no-textconv", "--find-renames",
            compare["base_sha"], compare["head_sha"]]


def changed_files(repo: Path, compare: dict) -> list[dict]:
    tokens = git(repo, [*_diff_args(compare), "--name-status", "-z", "--"]).decode("utf-8", "replace").split("\x00")
    rows = []
    index = 0
    while index < len(tokens) and tokens[index]:
        status, old = tokens[index:index + 2]
        index += 2
        new = old
        if status[0] in "RC":
            new = tokens[index]
            index += 1
        rows.append({"status": status[0], "path": new, "old_path": old,
                     "additions": 0, "deletions": 0, "binary": False})
    stats = git(repo, [*_diff_args(compare), "--numstat", "-z", "--"]).decode("utf-8", "replace").split("\x00")
    index = 0
    by_path = {row["path"]: row for row in rows}
    while index < len(stats) and stats[index]:
        added, removed, path = stats[index].split("\t", 2)
        index += 1
        if not path:
            path = stats[index + 1]
            index += 2
        if path in by_path:
            by_path[path].update(additions=int(added) if added.isdigit() else 0,
                                 deletions=int(removed) if removed.isdigit() else 0,
                                 binary=added == "-" or removed == "-")
    return rows


def file_diff(repo: Path, compare: dict, path: str, offset: int = 0, limit: int = 1200,
              context: int = 3) -> dict:
    files = changed_files(repo, compare)
    item = next((row for row in files if row["path"] == path), None)
    if not item:
        raise GitQueryError("文件不属于本次改动。")
    paths = list(dict.fromkeys([item["old_path"], item["path"]]))
    raw = text(repo, [*_diff_args(compare), f"--unified={context}", "--", *paths])
    old_line = new_line = 0
    in_hunk = False
    lines = []
    for line in raw.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        if line.startswith("@@ "):
            in_hunk = True
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                old_line, new_line = map(int, match.groups())
            lines.append({"kind": "hunk", "text": line, "old_line": None, "new_line": None})
        elif not in_hunk or line[:1] not in {" ", "+", "-"}:
            lines.append({"kind": "meta", "text": line, "old_line": None, "new_line": None})
        else:
            marker = line[0]
            lines.append({"kind": {"+": "add", "-": "delete", " ": "context"}[marker],
                          "text": line[1:], "old_line": old_line if marker != "+" else None,
                          "new_line": new_line if marker != "-" else None})
            old_line += marker != "+"
            new_line += marker != "-"
    return {"file": item, "lines": lines[offset:offset + limit], "total_lines": len(lines),
            "has_more": offset + limit < len(lines), "next_offset": min(offset + limit, len(lines))}
