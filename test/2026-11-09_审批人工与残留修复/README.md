# 2026-11-09 审批人工按钮与残留修复

## 背景

用户反馈两件事：

1. 开启「允许 AI 自动审核单次完全访问」后，单次完全访问请求仍显示人工「允许 / 拒绝」按钮；人工点允许后整体仍为 pending，必须等自动审核通过才执行，体验上像“点了没用”。
2. 审批面板收起后的「待审批操作」入口经常不消失：人工点过允许、等待 AI 审核或任务已结束时仍常驻。

## 根因

- `ToolApprovalPanel.vue` 的允许/拒绝按钮只判断 `historical`，不判断 `human_approval_required`。而服务端在单闸场景（`allow_auto_approve_full_access` 开启）明确下发 `human_approval_required=false`。
- `ToolApprovalManager.decide()` 对 full_access 无条件接受人工裁决，只记 `human_decision`，整体状态保持 pending。
- `InputComposer.vue` 的 `approvalRestoreVisible` 只判断 `toolApprovals.length > 0`，不区分“仍需人工决定 / 等待自动审核 / 已有裁决”。
- 待审批列表只在初始化、手动展开面板、移动端打开时拉快照，没有轮询；任务终态（complete/stopped/error）也没有清理残留，`tool_approval_resolved` 一旦漏消费就永久残留。

## 改动

| 文件 | 改动 |
|------|------|
| `static/src/components/panels/ToolApprovalPanel.vue` | 新增 `humanDecisionRequired`（`human_approval_required !== false`），为 false 时不渲染允许/拒绝按钮，仅展示 AI 审核进度 |
| `static/src/components/input/InputComposer.vue` | `approvalRestoreVisible` 改为 `toolApprovals.some(needsHumanDecision)` |
| `static/src/app/methods/taskPolling/approvals.ts` | 新增 `clearResidualToolApprovals()`：清空 pending/deciding、折叠面板、推进快照版本；保留审核记录 |
| `static/src/app/methods/taskPolling/completion.ts` | `task_complete` / `task_stopped` / fatal `task_error` 调用 `clearResidualToolApprovals()` |
| `modules/tool_approval_manager.py` | `decide()` 对 `human_approval_required=false` 的 full_access 抛 `ValueError`，不再接受无效人工裁决 |
| `modules/i18n_messages/managers.py` | 新增 `tool_approval.human_not_required` 中英文案 |

## 契约

- `human_approval_required=false` = 该请求只由自动审核决定（单闸），UI 不提供人工按钮，后端拒绝人工裁决。
- 双闸（`auto_approval` 模式 + 未开「允许 AI 自动审核单次完全访问」）保持不变：人工与自动各自独立通过，人工批准后仍需等自动审核，但“待审批操作”入口不再常驻。
- 审批终态仍只由 `tool_approval_resolved` 产生；`clearResidualToolApprovals` 只是任务终态兜底，不改变终态语义。

## 验证

- `npx tsc --noEmit` 通过
- 定向 eslint（4 个改动文件）0 warning
- `node scripts/i18n_audit.mjs` 通过
- `npm run build` 成功
- 后端：`python3.11 -B` 跑 `test/2026-11-05_完全访问审批/*`（27+10+4）、`test/2026-11-06_自动审核上下文/test_review_flow.py`（22）、`test/2026-11-07_无限制单次完全访问/test_unrestricted.py`（11）、本目录 `test_human_decision.py`（3）全部通过
- 前端：`test/2026-11-07_无限制单次完全访问/frontend.cjs`（3）、`test/2026-11-06_自动审核上下文/frontend.cjs`（3）、`test/2026-11-05_完全访问审批/frontend.cjs`（5）、`test/2026-10-07_运行对话快照/task_polling_tests.cjs`（18 组）、本目录 `frontend.cjs`（4）全部通过
- 视觉与真实模型链路由用户实机验收
