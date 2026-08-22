---
name: research-report
description: 调研报告流程：搜集、分析、撰写、校对
review_mode: readonly
max_stage_rounds: 15
end_conditions: 报告定稿且事实核查通过
nodes:
  - id: start-1
    kind: start
    name: 开始
    next: collect
  - id: collect
    kind: stage
    name: 资料搜集
    goal: 收集目标主题的权威资料
    instructions: 官方文档优先，其次第三方报道；记录 URL。
    next: analyze
  - id: analyze
    kind: stage
    name: 分析整理
    goal: 归纳核心观点与分歧
    instructions: 按主题分组，标注共识与争议点。
    next: draft
  - id: draft
    kind: stage
    name: 撰写初稿
    goal: 输出结构化报告初稿
    instructions: 先骨架后填充；每个结论标注来源类型。
    next: draft-gate
  - id: draft-gate
    kind: review
    name: 初稿审核
    prompt: 事实是否都有来源标注
    next: polish
    reject_to: collect
    max_rejects: 3
  - id: polish
    kind: stage
    name: 校对定稿
    goal: 事实核查与文字打磨
    instructions: 逐条核对来源；压缩冗余表述。
    next: polish-gate
  - id: polish-gate
    kind: review
    name: 终稿审核
    prompt: 结论确定性分级是否准确
    next: end-1
    reject_to: draft
    max_rejects: 3
  - id: end-1
    kind: end
    name: 结束
---

## 工作方式
信息必须标注来源。

## 验证方式
关键事实至少两个来源交叉验证。

## 结束方式
输出 markdown 报告。
