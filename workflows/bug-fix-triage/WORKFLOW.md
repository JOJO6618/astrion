---
name: bug-fix-triage
description: 缺陷分诊修复流程：复现确认、按严重程度三向分流、热修/常规双轨、统一回归把关
review_mode: active
max_stage_rounds: 30
end_conditions: 修复通过回归审核且更新日志落盘；或分诊为不跟进直接归档
nodes:
  - id: start-1
    kind: start
    name: 开始
    next: reproduce
  - id: reproduce
    kind: stage
    name: 复现确认
    goal: 稳定复现缺陷并确认影响面
    instructions: 记录复现步骤与环境；无法复现的缺陷退回报告人补充信息。
    next: severity-branch
  - id: severity-branch
    kind: branch
    name: 严重程度分诊
    next:
      - target: hotfix
        condition: 线上崩溃、数据丢失或安全漏洞，必须立即修复
      - target: fix
        condition: 功能异常但存在变通方法，随正常节奏修复
      - target: end-1
        condition: 影响极低、无法复现或重复反馈，记录后不再跟进
  - id: hotfix
    kind: stage
    name: 紧急热修
    goal: 以最小改动止血
    instructions: 只修阻断点，不做顺手重构；附带最小复现用例验证。
    next: hotfix-gate
  - id: hotfix-gate
    kind: review
    name: 热修审核
    prompt: 热修是否最小化且无副作用，复现用例是否真实跑过
    next: merge
    reject_to: hotfix
    max_rejects: 2
  - id: fix
    kind: stage
    name: 常规修复
    goal: 根治缺陷并补齐防护
    instructions: 定位根因后修复；必须补回归测试覆盖该缺陷路径。
    next: fix-gate
  - id: fix-gate
    kind: review
    name: 修复审核
    prompt: 是否根治而非掩盖症状，回归测试是否真实运行过
    next: merge
    reject_to: fix
    max_rejects: 3
  - id: merge
    kind: branch
    name: 汇总
    next:
      - target: regress-gate
        condition: ''
  - id: regress-gate
    kind: review
    name: 回归审核
    prompt: 整体回归是否通过，修复是否引入新问题
    next: document
    reject_to: severity-branch
    max_rejects: 2
  - id: document
    kind: stage
    name: 更新日志
    goal: 记录缺陷根因与修复内容
    instructions: 写入更新日志；标注受影响版本范围。
    next: end-1
  - id: end-1
    kind: end
    name: 结束
---

## 工作方式
先复现再分诊，按严重程度走对应修复轨道。

## 验证方式
热修必须有最小复现用例；常规修复必须补回归测试。

## 结束方式
回归审核通过后更新更新日志并归档。
