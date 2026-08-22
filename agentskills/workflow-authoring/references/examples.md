# 完整示例

三个典型结构的完整 WORKFLOW.md，可直接复制改造。目录：线性（无审核）→ 单审核 → 分支+并线+审核。

## 1. 线性流程（调研报告）

最小可用结构：start → 阶段链 → end。适合步骤固定、无需审核的收集整理类流程。

```markdown
---
name: research-report
description: 调研流程：收集资料、交叉验证、输出结构化报告
review_mode: active
max_stage_rounds: 25
end_conditions: 报告落盘且来源标注完整
nodes:
  - id: start-1
    kind: start
    name: 开始
    next: collect
  - id: collect
    kind: stage
    name: 资料收集
    goal: 围绕主题收集足够的一手资料
    instructions: 优先官方来源；每条资料记录出处；不下结论。
    next: verify
  - id: verify
    kind: stage
    name: 交叉验证
    goal: 剔除孤证与不可靠信息
    instructions: 关键事实至少两个独立来源；存疑内容明确标注。
    next: report
  - id: report
    kind: stage
    name: 输出报告
    goal: 生成结构化调研报告
    instructions: 结论先行，证据随后；每个结论标注来源。
    next: end-1
  - id: end-1
    kind: end
    name: 结束
---

## 工作方式
先广度收集，再深度验证，最后成文。

## 验证方式
关键事实双来源；报告结论均可回溯到证据。

## 结束方式
报告落盘并汇报要点。
```

## 2. 单审核流程（代码评审）

阶段链中嵌入 review 节点形成把关；驳回回到能整改的前序阶段。

```markdown
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
按阶段推进，每个阶段完成后汇报。

## 验证方式
每条评审意见有文件与行号；审核关注边界与安全。

## 结束方式
报告落盘并给出明确结论。
```

注意两个审核节点的 `reject_to` 选择：评审不充分 → 回到「代码探索」重新理解；报告问题 → 回到「逐项评审」，而不是机械地都回前一站。

## 3. 分支 + 并线 + 审核（功能开发）

多出线决策点（AI 按 condition 选路）+ 单出线并线器（汇合后统一审核）。

```markdown
---
name: feature-development
description: 功能开发流程：需求理解、按策略分支实现、汇总验证、文档收尾
review_mode: active
max_stage_rounds: 30
end_conditions: 验证通过且文档更新完成
nodes:
  - id: start-1
    kind: start
    name: 开始
    next: understand
  - id: understand
    kind: stage
    name: 需求理解
    goal: 明确需求边界与涉及模块
    instructions: 产出改动清单与影响面；不确定处先问用户。
    next: impl-branch
  - id: impl-branch
    kind: branch
    name: 实现策略
    next:
      - target: prototype
        condition: 方案不确定或风险高，先低成本验证
      - target: full-impl
        condition: 需求明确、改动范围清晰，直接正式实现
  - id: prototype
    kind: stage
    name: 快速原型
    goal: 最小成本验证方案可行性
    instructions: 只写关键路径，不做边界打磨。
    next: merge
  - id: full-impl
    kind: stage
    name: 完整实现
    goal: 完成正式代码修改
    instructions: 小步快跑，优先根治不打补丁。
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
    prompt: 验证证据是否真实可复现：构建与测试输出必须实际运行过
    next: document
    reject_to: impl-branch
    max_rejects: 3
  - id: document
    kind: stage
    name: 文档收尾
    goal: 更新相关文档
    instructions: 同步项目说明与相关文档。
    next: end-1
  - id: end-1
    kind: end
    name: 结束
---

## 工作方式
先理解再动手，禁止跳过验证。

## 验证方式
构建通过 + 相关测试通过。

## 结束方式
更新文档并汇报。
```

设计要点：分支的两条 `condition` 互斥可判断；并线器 `merge` 让两条实现路径汇合后共用同一套审核与收尾；审核驳回回到「实现策略」决策点，允许换条路重新实现。
