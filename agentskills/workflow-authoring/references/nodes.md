# 节点详解（五种 kind）

工作流拓扑为**串行**（无并行语义）。所有路由必须显式连接——`next` 留空会导致保存校验失败；任何路由不能指向 start 节点。

## start（入口，恰好 1 个）

```yaml
- id: start-1
  kind: start
  name: 开始
  next: explore        # 必填：第一个节点的 id
```

## end（终点，至少 1 个）

```yaml
- id: end-1
  kind: end
  name: 结束
```

无 `next`。流程推进到 end 即完成，执行者收到「输出总结后结束」的指令。可以有多个 end（不同路径收束到各自终点）。

## stage（执行阶段）

```yaml
- id: explore
  kind: stage
  name: 代码探索
  goal: 理解改动范围，产出影响面清单
  instructions: 先读 git diff 总览，再按模块深入；禁止通读整文件。
  next: verify-gate    # 必填：可指向 stage/review/branch/end
```

- 阶段内执行者**完全自主**：自由调用工具、跑命令、读写文件；拓扑不约束阶段内行为
- `goal` 一句话目标；`instructions` 具体要求——两者在激活与每次推进时**全文呈现**给执行者，写关键约束，控制长度
- 审核节点把关时，`goal`/`instructions` 是「这个阶段应该做到什么」的标尺，写可检查的要求
- `max_stage_rounds` 限制单阶段轮数；撞限不中断流程，执行者会停下来向用户确认是否继续

## review（审核节点）

```yaml
- id: verify-gate
  kind: review
  name: 验证审核
  prompt: 验证证据是否真实可复现：构建与测试输出必须实际运行过
  next: document        # 必填：通过后的去向
  reject_to: explore    # 必填：驳回后回到哪里整改
  max_rejects: 3        # 连续驳回上限，默认 3，必须 ≥ 1
```

- **瞬态节点**：阶段汇报到达时同步完成审核，通过/驳回立即走到下一站，流程不会在 review 上停留
- `prompt` 是写给审核者的**关注点**（重点查什么），不是流程描述；审核者能看到阶段执行痕迹与执行者的汇报
- 连续驳回达到 `max_rejects`，整个工作流以 failed 终止
- 审核服务异常按驳回处理（计入驳回计数）
- 想要「结束前总审核」：在 end 前显式放一个 review 节点，终点没有隐藏审核

## branch（分支 / 并线器）

```yaml
# 多出线 = AI 决策点
- id: strategy
  kind: branch
  name: 策略选择
  next:
    - target: prototype
      condition: 方案不确定或风险高，先低成本验证
    - target: full-impl
      condition: 需求明确、改动范围清晰，直接正式实现

# 单出线 = 并线器（自动穿过）
- id: merge
  kind: branch
  name: 汇总
  next:
    - target: verify-gate
      condition: ''
```

- **多出线**：流程推进到此停下，执行者根据各出线的 `condition` 自主选择一条路径。`condition` 必填，是写给 AI 的决策依据——条件间互斥、可判断、覆盖常见情形
- **单出线**：并线器，自动穿过不做决策，`condition` 留空；典型用途是多条路径汇合后统一进 review 或 end
- 分支可以指向分支（连续决策），也可以回指形成循环（配合 review 驳回实现整改闭环）

## 通用字段说明

| 字段 | 说明 |
|---|---|
| `id` | 节点唯一标识（路由引用用它），建议 kebab-case |
| `name` | 显示名（进度展示用），可中文 |
| `kind` | 五种之一，缺省会按 stage 解析但应始终显式写 |
| `position` | 画布坐标，仅可视化编辑器使用；手写可省略，编辑器打开时会自动排版 |

## 语义速记（影响拓扑设计）

- 流程推进是「汇报驱动」：执行者完成当前阶段后主动汇报，引擎按当前阶段的 `next` 走向下一站
- review 驳回形成「整改循环」：`reject_to` 指回能真正整改的阶段（不一定是紧邻前一站）
- 已激活的运行实例按激活时的文档版本执行；修改文档后需重新激活才生效
- 工作流是执行者的辅助流程而非宿主：运行期间用户可自由穿插讨论，也可随时退出
