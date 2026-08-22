---
name: workflow-authoring
description: 编写、创建或修改工作流（workflow）定义文件 WORKFLOW.md 时使用。工作流把一套既定流程（阶段、审核、分支、结束方式）存为可复用文档，激活后智能体按流程逐步推进。当用户要求「新建一个工作流」「写一个 xx 流程」「调整工作流结构（增删阶段/审核/分支）」，或需要排查工作流格式与校验问题时使用。附带初始化与校验脚本。
---

# 工作流（Workflow）文档编写指南

工作流 = 一份 `WORKFLOW.md`：YAML frontmatter 描述节点拓扑，markdown 正文写流程约定。核心理念：**结构在边上，自主在阶段内**——拓扑只约束阶段间流转，每个执行阶段内智能体自由工作，阶段完成后汇报推进。

## 标准创建流程

1. **初始化模板**：
   ```bash
   python3 scripts/init_workflow.py <name> [--path <目录>] [--template linear|review|branch]
   ```
   生成 `<目录>/<name>/WORKFLOW.md`（已存在不覆盖）。三种模板：linear 线性 / review 带审核 / branch 带分支。
2. **填写内容**：替换模板中所有 `[方括号]` 占位，按需增删节点。
3. **保存前自检**：
   ```bash
   python3 scripts/validate_workflow.py <name>
   ```
   有 error 级问题必须全部修掉，否则保存会被拒绝。
4. 校验通过后交由保存流程（工作流名即 `<name>`，合法字符：小写字母/数字/连字符，首字符为字母或数字）。

## WORKFLOW.md 格式骨架

```markdown
---
name: my-pipeline
description: 一句话说明这个流程干什么
review_mode: active
max_stage_rounds: 20
end_conditions: 什么算完成
nodes:
  - id: start-1
    kind: start
    name: 开始
    next: explore
  - id: explore
    kind: stage
    name: 代码探索
    goal: 理解改动范围，产出影响面清单
    instructions: 先读 git diff 总览，再按模块深入；禁止通读整文件。
    next: verify-gate
  - id: verify-gate
    kind: review
    name: 验证审核
    prompt: 验证证据是否真实可复现
    next: end-1
    reject_to: explore
    max_rejects: 3
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

`---` 之后的正文会在激活时原文呈现给执行者——写各阶段通用的整体约定，别写节点级细节。

## frontmatter 字段

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `name` | ✓ | — | 小写字母/数字/连字符 |
| `description` | 推荐 | `''` | 一句话用途，出现在流程选择菜单 |
| `review_mode` | | `active` | `active`：审核时可查证证据；`readonly`：审核只看汇报与记录 |
| `max_stage_rounds` | | `20` | 单阶段轮数上限；撞限不停流程，只会让执行者停下来向用户确认是否继续 |
| `end_conditions` | | `''` | 完成标准的声明性描述（展示用） |
| `nodes` | ✓ | — | 节点数组，五种节点见 references/nodes.md |

## 节点速查

| kind | 语义 | 关键字段 |
|---|---|---|
| `start` | 入口，恰好 1 个 | `next` |
| `end` | 终点，至少 1 个 | — |
| `stage` | 执行阶段（阶段内完全自主） | `goal` + `instructions` + `next` |
| `review` | 审核把关（汇报时同步审完立即走，不停留） | `prompt` + `next` + `reject_to` + `max_rejects` |
| `branch` | 单出线=并线器自动穿过；多出线=AI 决策点（`condition` 必填为决策依据） | `next[]`（target/condition） |

**字段详解与语义**：见 [references/nodes.md](references/nodes.md)
**完整示例**（线性 / 带审核 / 分支+并线）：见 [references/examples.md](references/examples.md)

## 校验规则（违反即保存失败，validate 脚本同款）

1. `name` 非空且合法；节点 id 全局唯一
2. 恰好 1 个 start；至少 1 个 end
3. start/stage/review 的 `next`、review 的 `reject_to`、branch 每条出线的 `target` 都必须显式连接、指向存在的节点、且不指向 start
4. `max_rejects ≥ 1`；多出线 branch 的每条 `condition` 必填

## 写作建议

- 阶段 3~7 个为宜；`goal` 一句话、`instructions` 三五条关键约束（这两者在每次推进时会全文呈现给执行者）
- 需要把关就显式放 review 节点——终点前不会自动审核
- `reject_to` 指回真正能整改问题的阶段，不要指回紧邻前一站走过场
- 分支 `condition` 写给 AI 的决策依据：互斥、可判断、覆盖常见情形
- 修改已激活的工作流文档不影响正在运行的实例——运行中的实例按激活时的版本执行，需退出后重新激活才用新版本
