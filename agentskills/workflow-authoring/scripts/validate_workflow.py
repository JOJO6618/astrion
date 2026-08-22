#!/usr/bin/env python3
"""
Workflow Validator - 校验 WORKFLOW.md 的格式与结构

Usage:
    validate_workflow.py <WORKFLOW.md 或其所在目录>

校验内容：
    - YAML frontmatter 存在且可解析；name 为合法 slug
    - review_mode / max_stage_rounds 取值合法
    - 节点：id 唯一、恰好 1 个 start、至少 1 个 end
    - 各 kind 字段齐全（stage/review/branch/start 的必填项）
    - 路由显式连接、指向存在的节点、不指向 start
    - max_rejects 为 ≥1 的整数

退出码：0 = 通过；1 = 有 error 级问题。
保存工作流时服务端会做同等校验，本脚本用于保存前自检。
"""

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("错误：需要 PyYAML（pip install pyyaml）")
    sys.exit(1)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
NODE_KINDS = ("start", "end", "stage", "review", "branch")
REVIEW_MODES = ("active", "readonly")


def locate(path_str: str) -> Path:
    path = Path(path_str).expanduser().resolve()
    if path.is_dir():
        path = path / "WORKFLOW.md"
    return path


def validate(text: str) -> list:
    errors = []
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not match:
        return ["WORKFLOW.md 缺少 YAML frontmatter（文件必须以 --- 包裹的 YAML 开头）"]
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return [f"YAML frontmatter 解析失败：{exc}"]
    if not isinstance(meta, dict):
        return ["YAML frontmatter 必须是键值对结构"]

    name = str(meta.get("name") or "").strip()
    if not name:
        errors.append("缺少 name")
    elif not NAME_RE.match(name):
        errors.append(f"name 不合法：{name!r}（仅限小写字母/数字/连字符，首字符为字母或数字）")

    review_mode = meta.get("review_mode")
    if review_mode is not None and review_mode not in REVIEW_MODES:
        errors.append(f"review_mode 只能是 active 或 readonly（当前 {review_mode!r}）")

    rounds = meta.get("max_stage_rounds")
    if rounds is not None and (not isinstance(rounds, int) or rounds < 1):
        errors.append(f"max_stage_rounds 必须是 ≥1 的整数（当前 {rounds!r}）")

    nodes = meta.get("nodes") or []
    if not nodes:
        errors.append("至少需要一个开始节点和一个结束节点")
        return errors

    by_id = {}
    for n in nodes:
        if not isinstance(n, dict):
            errors.append("nodes 数组中每个元素必须是键值对结构")
            continue
        nid = n.get("id")
        if not nid:
            errors.append(f"存在缺少 id 的节点（kind={n.get('kind')!r}）")
            continue
        if nid in by_id:
            errors.append(f"节点 id 重复：{nid}")
        by_id[nid] = n
        if n.get("kind") not in NODE_KINDS:
            errors.append(f"节点 {nid} 的 kind 非法：{n.get('kind')!r}（可选 {NODE_KINDS}）")

    starts = [n for n in by_id.values() if n.get("kind") == "start"]
    ends = [n for n in by_id.values() if n.get("kind") == "end"]
    if len(starts) == 0:
        errors.append("缺少开始节点")
    elif len(starts) > 1:
        errors.append(f"开始节点只能有一个（当前 {len(starts)} 个）")
    if not ends:
        errors.append("缺少结束节点")

    def check_ref(owner, target, label):
        if target is None or target == "":
            errors.append(f"{label}未连接")
        elif target not in by_id:
            errors.append(f"{label}指向不存在的节点：{target}")
        elif by_id[target].get("kind") == "start":
            errors.append(f"{label}不能指向开始节点")

    for n in by_id.values():
        kind = n.get("kind")
        label_name = n.get("name") or n.get("id")
        if kind == "start":
            check_ref(n, n.get("next"), "开始节点")
        elif kind == "stage":
            if not str(n.get("goal") or "").strip():
                errors.append(f"阶段「{label_name}」缺少 goal")
            if not str(n.get("instructions") or "").strip():
                errors.append(f"阶段「{label_name}」缺少 instructions")
            check_ref(n, n.get("next"), f"阶段「{label_name}」")
        elif kind == "review":
            if not str(n.get("prompt") or "").strip():
                errors.append(f"审核「{label_name}」缺少 prompt（审核关注点）")
            check_ref(n, n.get("next"), f"审核「{label_name}」的通过路由")
            check_ref(n, n.get("reject_to"), f"审核「{label_name}」的驳回路由")
            max_rejects = n.get("max_rejects", 3)
            if not isinstance(max_rejects, int) or max_rejects < 1:
                errors.append(f"审核「{label_name}」驳回上限 max_rejects 必须 ≥ 1")
        elif kind == "branch":
            routes = n.get("next") or []
            if not routes:
                errors.append(f"分支「{label_name}」没有任何出线")
            for r in routes:
                if not isinstance(r, dict):
                    errors.append(f"分支「{label_name}」的出线必须是 target/condition 结构")
                    continue
                check_ref(n, r.get("target"), f"分支「{label_name}」的出线")
            if len(routes) > 1:
                for r in routes:
                    if isinstance(r, dict) and not str(r.get("condition") or "").strip():
                        errors.append(f"分支「{label_name}」多出线的 condition 必填（AI 决策依据）：→ {r.get('target')}")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    target = locate(sys.argv[1])
    if not target.exists():
        print(f"错误：文件不存在 {target}")
        return 1
    errors = validate(target.read_text(encoding="utf-8"))
    if errors:
        print(f"校验未通过（{len(errors)} 个问题）：")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"校验通过：{target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
