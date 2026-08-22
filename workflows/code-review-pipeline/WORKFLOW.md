---
name: code-review-pipeline
description: 代码评审标准流程：探索改动、逐项评审、输出结构化报告
review_mode: active
max_stage_rounds: 20
end_conditions: 评审报告落盘且最终审核通过
nodes:
  - id: start-1
    kind: start
    name: 开始
    next: explore
  - id: explore
    kind: stage
    name: 代码探索
    goal: 理解改动范围与相关模块，产出影响面清单
    instructions: 先读 git diff 总览，再按模块逐个深入；禁止通读整文件。
    next: review
  - id: review
    kind: stage
    name: 逐项评审
    goal: 按 checklist 评审每个改动文件
    instructions: 关注边界条件、错误处理、安全问题；每条意见标注文件与行号。
    next: review-gate
  - id: review-gate
    kind: review
    name: 评审审核
    prompt: 检查是否遗漏边界条件和安全问题
    next: report
    reject_to: explore
    max_rejects: 3
  - id: report
    kind: stage
    name: 输出报告
    goal: 生成结构化评审报告并落盘
    instructions: 按「严重/建议/可选」三档组织；给出明确结论。
    next: report-gate
  - id: report-gate
    kind: review
    name: 报告审核
    prompt: 报告结论是否与评审意见一致
    next: end-1
    reject_to: review
    max_rejects: 3
  - id: end-1
    kind: end
    name: 结束
---

## 工作方式
按阶段推进，每个阶段完成后调用阶段汇报工具。

## 验证方式
关键结论必须有代码行号或命令输出佐证。

## 结束方式
报告写入 output/ 目录并汇报完成。
