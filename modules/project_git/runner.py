"""Bounded, read-only local Git queries. No shell, fetch, hooks or textconv."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


class GitQueryError(ValueError):
    pass


def git(repo: Path, args: list[str], *, input_bytes: bytes | None = None,
        timeout: int = 15, max_bytes: int = 32 * 1024 * 1024) -> bytes:
    executable = shutil.which("git")
    if not executable:
        raise GitQueryError("未安装 Git，无法读取项目记录。")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_LITERAL_PATHSPECS="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    command = [executable, "--no-pager", "-c", "core.quotePath=false",
               "-c", "core.fsmonitor=false", "-c", "color.ui=false",
               "-c", "log.showSignature=false", "-c", f"safe.directory={repo}",
               "-C", str(repo), *args]
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            proc = subprocess.run(command, input=input_bytes, stdout=output, stderr=errors,
                                  env=env, timeout=timeout, check=False)
            if proc.returncode:
                errors.seek(0)
                detail = errors.read(2000).decode("utf-8", "replace").strip()
                raise GitQueryError(detail or "Git 查询失败。")
            if output.tell() > max_bytes:
                raise GitQueryError("内容超过单次读取上限，请缩小查询范围或选择较小的文件。")
            output.seek(0)
            return output.read()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitQueryError("Git 查询超时或目录不可访问。") from exc


def text(repo: Path, args: list[str], **kwargs) -> str:
    return git(repo, args, **kwargs).decode("utf-8", "replace")


def commit_sha(repo: Path, ref: str) -> str:
    if not isinstance(ref, str) or not ref or len(ref) > 512 or ref.startswith("-") or any(c in ref for c in "\x00\r\n"):
        raise GitQueryError("无效的提交或分支。")
    return text(repo, ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"]).strip()


def empty_tree(repo: Path) -> str:
    return git(repo, ["hash-object", "-t", "tree", "--stdin"], input_bytes=b"").decode().strip()
