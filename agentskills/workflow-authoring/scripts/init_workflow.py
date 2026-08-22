#!/usr/bin/env python3
"""
Workflow Initializer - 生成新工作流的 WORKFLOW.md 模板

Usage:
    init_workflow.py <name> [--path <目录>] [--template linear|review|branch]

Examples:
    init_workflow.py my-pipeline
    init_workflow.py code-check --path ./drafts --template review
    init_workflow.py feature-flow --template branch

模板说明：
    linear  线性流程：start → stage → stage → end
    review  带审核：在实现阶段后加审核节点，驳回回到整改阶段
    branch  带分支：策略分支（双出线决策点）+ 并线器 + 审核
"""

import argparse
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

HEADER = """---
name: {name}
description: [一句话说明这个流程干什么]
review_mode: active
max_stage_rounds: 20
end_conditions: [什么算完成：验证标准与收尾动作]
nodes:
"""

FOOTER = """---

## 工作方式
[整体工作原则：各阶段通用的约束]

## 验证方式
[如何验证成果：要跑什么命令/检查什么证据]

## 结束方式
[收尾动作：文档、汇报等]
"""

TEMPLATES = {
    "linear": """  - id: start-1
    kind: start
    name: 开始
    next: step-1
  - id: step-1
    kind: stage
    name: 阶段一
    goal: [阶段目标，一句话]
    instructions: [阶段要求与关键约束]
    next: step-2
  - id: step-2
    kind: stage
    name: 阶段二
    goal: [阶段目标，一句话]
    instructions: [阶段要求与关键约束]
    next: end-1
  - id: end-1
    kind: end
    name: 结束
""",
    "review": """  - id: start-1
    kind: start
    name: 开始
    next: implement
  - id: implement
    kind: stage
    name: 实现
    goal: [阶段目标，一句话]
    instructions: [阶段要求与关键约束]
    next: verify-gate
  - id: fix
    kind: stage
    name: 整改
    goal: 按审核意见修复问题
    instructions: 逐条处理审核意见，说明每条的处置方式。
    next: verify-gate
  - id: verify-gate
    kind: review
    name: 验证审核
    prompt: [审核关注点：重点检查什么]
    next: end-1
    reject_to: fix
    max_rejects: 3
  - id: end-1
    kind: end
    name: 结束
""",
    "branch": """  - id: start-1
    kind: start
    name: 开始
    next: prepare
  - id: prepare
    kind: stage
    name: 准备
    goal: [阶段目标，一句话]
    instructions: [阶段要求与关键约束]
    next: strategy
  - id: strategy
    kind: branch
    name: 策略选择
    next:
      - target: path-a
        condition: [走 A 路径的条件，写给 AI 的决策依据]
      - target: path-b
        condition: [走 B 路径的条件，与 A 互斥]
  - id: path-a
    kind: stage
    name: 路径 A
    goal: [阶段目标]
    instructions: [阶段要求]
    next: merge
  - id: path-b
    kind: stage
    name: 路径 B
    goal: [阶段目标]
    instructions: [阶段要求]
    next: merge
  - id: merge
    kind: branch
    name: 汇总
    next:
      - target: verify-gate
        condition: ''
  - id: verify-gate
    kind: review
    name: 验证审核
    prompt: [审核关注点]
    next: end-1
    reject_to: strategy
    max_rejects: 3
  - id: end-1
    kind: end
    name: 结束
""",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成新工作流的 WORKFLOW.md 模板")
    parser.add_argument("name", help="工作流名（小写字母/数字/连字符）")
    parser.add_argument("--path", default=".", help="目标父目录（默认当前目录）")
    parser.add_argument("--template", default="linear", choices=sorted(TEMPLATES), help="模板类型")
    args = parser.parse_args()

    name = args.name.strip()
    if not NAME_RE.match(name):
        print(f"错误：工作流名不合法 {name!r}（仅限小写字母/数字/连字符，首字符为字母或数字）")
        return 1

    target_dir = Path(args.path).expanduser().resolve() / name
    target_file = target_dir / "WORKFLOW.md"
    if target_file.exists():
        print(f"错误：{target_file} 已存在（不会覆盖）")
        return 1

    target_dir.mkdir(parents=True, exist_ok=True)
    content = HEADER.format(name=name) + TEMPLATES[args.template] + FOOTER
    target_file.write_text(content, encoding="utf-8")
    print(f"已创建 {target_file}")
    print("下一步：填写 [方括号] 占位内容，然后运行 validate_workflow.py 自检。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
