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
    instructions: 同步 AGENTS.md 与相关 doc。
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
