"""Small workspace-scoped index of review conversations, outside source trees."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import threading
import os

_LOCK = threading.RLock()


def _path(data_dir: Path, workspace_id: str, repo_id: str) -> Path:
    scope = hashlib.sha256(workspace_id.encode()).hexdigest()[:24]
    key = hashlib.sha256(repo_id.encode()).hexdigest()[:24]
    return Path(data_dir) / "project_git_reviews" / scope / f"{key}.json"


def read_reviews(data_dir: Path, workspace_id: str, repo_id: str) -> list[dict]:
    path = _path(data_dir, workspace_id, repo_id)
    with _LOCK:
        if not path.exists():
            return []
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("评审记录格式损坏。")
        return rows


def record_review(data_dir: Path, workspace_id: str, repo_id: str, review: dict) -> None:
    path = _path(data_dir, workspace_id, repo_id)
    with _LOCK:
        rows = read_reviews(data_dir, workspace_id, repo_id)
        rows.insert(0, review)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".review-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(rows[:200], stream, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def review_prompt(repo: Path, compare: dict) -> str:
    if compare["kind"] == "commit":
        target = f"提交 {compare['head_sha']}\n提交说明：{compare['commit']['subject']}"
    else:
        target = f"分支 {compare['head_ref']} 相对 {compare['base_ref']} 的改动"
    return (
        f"请你 review 在 {repo}/ 目录下的{target}。\n"
        f"目标版本：{compare['head_sha']}\n对比基准：{compare['base_sha']}\n\n"
        "这是已授权立即开始的代码评审任务，请直接调查并完成评审。\n"
        "使用上面的完整 SHA 固定评审范围，通过 git diff BASE HEAD -- 查看差异，"
        "通过 git show SHA:路径 读取对应版本的相关代码，不能用当前工作树代替历史版本。"
        "根提交的基准是空树。分支范围已经固定为开始评审时的两个版本。\n"
        "读取适用的 AGENTS.md 及其要求的细则，检查调用关系、接口契约、边界条件、"
        "错误处理、并发、数据丢失、安全和明显性能退化。\n"
        "只报告本次改动引入、证据充分、作者值得修复的具体问题；"
        "避免推测、重复意见和无关的风格建议。每条问题说明 P0/P1/P2/P3 优先级、"
        "目标版本的文件与行号、触发条件、实际影响和证据，必要时引用项目规则。"
        "没有发现明确问题时直接说明，并说明实际检查范围及未验证项。\n"
        "本轮只评审，不修改源码、不 checkout/reset/stash/commit/push，也不安装依赖。"
        "如需运行测试，应先确认测试针对目标版本且不会修改当前工作树；"
        "无法在只读环境验证时明确说明，不把未执行的测试视为通过。"
    )
