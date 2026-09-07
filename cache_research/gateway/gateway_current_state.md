# Astrion Gateway 架构现状文档（修订版 v2）

> 生成日期：2026-09-07（同日经外部模型审阅修订；13 条批注 G1-G13 已消化进正文，消化对照见附录 §8.2）
>
> **本轮目标锚定（G1）**：以**四层架构 + CLI/GUI/IDE 插件独立接入**为目标。前期「先用定时任务验证公共入口」的路线适用于当时唯一明确的新调用方；现在优先级调整为：Gateway 独立启动 → 公共协议与多客户端交互闭环 → 正式客户端接入；定时任务随后接入该边界（见 §7）。
>
> 定位：回答一个问题——**对照四层架构愿景，当前代码实际做到哪一步、差距在哪、通往「完整 Gateway」还剩哪些工作。** 本文只做现状盘点与路线规划，不做实现。
>
> **信息来源（三方交叉验证）**：
> 1. 愿景文档：`.astrion/user_upload/Astrion_Architecture_Product_Roadmap_Review_1.md`（§10-15、§16-18、§35-38、§41-43）
> 2. 契约与研究：`docs/runtime_contract.md`、`cache_research/gateway/`（work_plan / phase12_implementation_plan / eval_summary + 四份子报告）
> 3. 2026-09-07 当日三份只读代码核查（全文见附录 §8.1）：
>    - `audit_entry_points_v2/entry_points_audit.md`（任务入口收敛核查）
>    - `audit_state_ownership_v2/state_ownership_audit.md`（状态责任表 S1-S10 落地核查）
>    - `audit_research_summary_v2/research_summary.md`（既有研究汇总 + G1-G18 遗留全集）
> 4. 当日实测：改造相关测试复跑 **25/25 全绿**（unittest 方式）；`git log` 确认改动已提交；G4 依赖链论断经主智能体亲自复验（§3.3）。

---

## 1. 愿景参照系

### 1.1 四层目标架构（§10.1 / §43 合并）

```text
┌──────────────────────────────────────────────┐
│  Client Layer                                │
│  Web / Desktop / CLI/TUI / IDE / Android / SDK
└──────────────────────┬───────────────────────┘
                       │  Stable Runtime Protocol
┌──────────────────────▼───────────────────────┐
│  Gateway                                     │
│  identity/auth · session ownership · run/task
│  lifecycle · routing · approval routing ·
│  event stream · reconnect/persistence · policy
└──────────────────────┬───────────────────────┘
                       │  Runtime Internal API
┌──────────────────────▼───────────────────────┐
│  Agent Runtime                               │
│  agent loop · context/compaction · workflow ·
│  sub-agent/AgentSession · memory · tool planning
└──────────────────────┬───────────────────────┘
                       │  Execution Contract
┌──────────────────────▼───────────────────────┐
│  Execution Plane                             │
│  Host Sandbox / Docker / Remote Worker /     │
│  Browser / Tool Broker                       │
└──────────────────────────────────────────────┘
```

### 1.2 四层职责划分（G2 澄清）

| 层 | 职责 |
|---|---|
| 前端 | 输入、展示、状态投影 |
| Gateway | 身份与资源授权、会话管理、Run 受理与可查询生命周期、审批裁决、订阅与恢复 |
| Agent Runtime | 模型循环、上下文、工作流、子智能体编排、工具调用决策；向 Gateway 报告执行进展 |
| 执行环境 | 具体工具执行，落实路径/网络/沙箱限制，返回结构化结果与取消结果 |

层间接口：Gateway↔Runtime 使用明确的执行上下文、事件输出与交互接口；Runtime↔执行环境使用 Execution Contract。**Gateway 可以内部委托多个 manager 管理状态——无需把所有数据放进一个大类，也无需将四层拆成四个进程。**

### 1.3 关键设计原则（Roadmap 原文立场）

| 原则 | 出处 | 要点 |
|---|---|---|
| 逻辑边界先行，不拆微服务 | §11 | 先做进程内 `RuntimeService` 类边界，Flask 只是 Adapter；**先稳定 contract，再决定 deployment topology** |
| Transport 与 Protocol 分离 | §13 | 协议语义同一，传输按通道裁剪（HTTP/WS、stdio/socket 均可承载）；**客户端只理解稳定 protocol，不理解 Agent 内部实现** |
| State 只有一个 Owner | §14 | Gateway owns session/run/approval/event history/task state；Clients 只是 state projection |
| 去 Flask 化语义（≠不用 Flask） | §37 | 依赖方向单向：Flask → Gateway Service → Runtime Service → Core/Modules；禁止反向 import 读 request/cookie |
| 正式 Runtime Contract 文档 | §38 | 先定义 Entities/Commands/Events，再选传输 |
| 验收标准：Phase B | §41 | 见本文 §4.2 逐项对照 |

### 1.4 协议原语：现有基础与真实缺口（G3 口径）

Roadmap §12 的四个原语与当前实现的映射：

| 原语 | 当前基础 | 真实缺口 |
|---|---|---|
| Session（长期会话） | conversation 已有（id/user/workspace/metadata/持久化 JSON） | 公共契约中的字段子集与生命周期语义未定义 |
| Turn（一轮执行） | Run/task 已有（TaskRecord，pending/running/终态） | **未采用 Turn 命名本身不是架构缺口**；真正要做的是主 Run、子智能体任务、后台执行的**映射关系明确**，不能只按名称合并 |
| Item（UI 可见事件统一单元） | 事件类型已事实存在（assistant/tool_call/approval...） | Item 的稳定标识、内容更新与生命周期（started/delta/completed）未定义；应**逐步适配现有事件**，避免同时重写所有前端渲染 |
| Event（Server→Client 通知） | task 级 idx/offset 协议 + socket 推送已有 | 序号作用域、事件覆盖范围、授权订阅、缺口检测、快照水位与续传衔接未定义（详见 §7 第 3 步） |

公共协议还需明确（当前均未定义）：**Commands、Queries、Events、授权范围、错误码、请求重试与兼容版本策略**——不能只列实体字段。

---

## 2. 当前进度总览

### 2.1 三阶段路线状态（gateway_work_plan.md，2026-09-07 修订版）

| 阶段 | 目标 | 状态 | 证据 |
|---|---|---|---|
| 阶段一 · 固定契约 | 状态责任表 + RuntimeService 契约 + 回归用例 | ✅ 完成 | `docs/runtime_contract.md`（143 行） |
| 阶段二 · 公共任务入口 | RuntimeContext + RuntimeService + 6 处调用方迁移 + 拆 Flask 桥 | ✅ 完成（但见 §3.3 的边界收窄） | `server/runtime/` 包；commit `652c1606` |
| 阶段三 · 定时任务 | Schedule/Occurrence 实体 + 幂等/重叠/停机语义 + 持久化 | ⬜ 未做，**本轮路线重排后后置**（§7.6） | 无任何调度器代码 |

**提交状态**：阶段一/二改动**已提交**（`652c1606`，前置 `7d290d17`/`6e043389`，后续还有 `9a17b381`/`c80bc4fb`/`b4deee0f` 三个修复）。

**测试现状**：改造相关测试 **25/25 全绿**（当日 unittest 复跑；项目 .venv 无 pytest）。构成：`test_server_refactor_smoke`(6) + `test_runtime_service`(10) + `test_conversation_model_persistence`(4) + `test_runtime_identity_resources`(4) + 新增 1 例。存量失败 4 项与本次改造无关。
**测试覆盖口径警示（G13）**：`test_runtime_service.py:117-118` 将 `task_manager._run_chat_task` 替换为空 lambda（线程即刻结束）——入口测试**绕过了真实执行装配**，不能以这类测试通过认定边界完成；后续验收必须覆盖真实执行装配与生命周期。

### 2.2 范围评估的关键数字（eval_summary，经 R1-R6 审阅修订）

- 全部 Flask 端点 **196 个**：必须迁移的 **16 个（8.2%）** = T1 任务受理 3 + T2 任务控制 13；其余 180 个（观察/CRUD/状态/认证）不需要迁移。
- 16 个端点复杂度：12 小 / 3 中 / 1 大（唯一大项 = workflow activate）。
- 四支撑链路评估：事件/取消/保存可直接复用（需适配层），**审批链路需改造**。
- **口径警示（G6）**：16 个端点是「任务受理/控制」的范围估算，**不是多端独立接入所需的公共能力范围上限**（见 §4.3）。

---

## 3. 已实现清单（阶段一/二交付物）

### 3.1 契约层（阶段一）

- `docs/runtime_contract.md`：概念对齐（Session=conversation / Run=一轮主任务 / Event=task events 条目）；状态责任表 S1-S10；已有正确性保障 6 项；RuntimeService 契约；调用方迁移表；回归用例 T01-T12。

### 3.2 入口层（阶段二，当日核查验证）

- **`server/runtime/` 包**（新）：
  - `context.py`（197 行）：RuntimeContext 三层模型——`TrustedPrincipal`（只能由适配层认证后构造）/ `TaskParams` / `InternalDirectives`（普通客户端不可提交）；**不 import flask**。
  - `service.py`（114 行）：`RuntimeService` 无状态薄层——`create_task / cancel_task / guidance 与 pending 队列×4 / get_task / get_task_events`；进程级单例；**不持有任务状态**。
- **入口收敛（当日验证，声明属实）**：
  - 全部 6 处任务创建调用点已迁移到 `runtime_service.create_task(ctx)`：`server/tasks/api.py:220`、`server/api_v1.py:335`、`server/workflow_runtime_api.py:192/:269`、`server/chat_flow_task_main.py:618/:1417`。
  - **无漏网调用点**：`create_chat_task` 实体调用全仓仅 2 处 = 定义 `models.py:128` + 委托 `service.py:38`。
  - `create_chat_task` 强制显式 session_data，缺失抛 ValueError（`models.py:174-175`）。
- **执行链运行期解耦（当日验证属实）**：`_run_chat_task` 不再建立 `test_request_context`，任务线程全程 `RuntimeIdentity` 驱动（`server/context/identity.py`）；server/core/modules/utils 下 `test_request_context` 实体调用 = 0；`models.py` 无 `session[` 读取。
- **依赖方向（结论收窄，见 §3.3）**：`server/context.py`（989 行）已拆分为 `server/context/` 子包；`get_user_resources` 已参数化（RuntimeIdentity 显式身份快照）。**「符合单向依赖」的结论仅适用于「任务所需运行期上下文已显式化」这一层。**
- **审批超时透传管道**：terminal `_approval_timeout_seconds` → 工具循环 `_approval_timeout_for()` → 4 个 `_wait_*` 调用点（默认 3600s 语义不变）。

### 3.3 独立启动缺口（G4，当日主智能体复验属实）

**论断**：移除 test_request_context ≠ 独立服务初始化与依赖解耦完成。**运行期**上下文已显式化，但**模块加载期**依赖仍锚定 Web 栈：

```text
runtime_service.create_task()
  → from server.tasks import task_manager     # service.py:35 等 8 处延迟导入
    → server/tasks/__init__.py:3              # from flask import Blueprint；创建 tasks_bp；级联 import 路由模块
      → server/tasks/models.py:15             # from server.chat_flow import run_chat_task_sync
        → server/chat_flow.py:19/55/73        # 导入 Flask(request/session)、认证辅助、socketio
```

**后果**：任何无 Web 的进程想使用 RuntimeService，import 时仍会拉起整条 Flask/SocketIO 依赖链——「不启动 Web 应用、独立初始化 Gateway/Runtime」目前做不到。

**下一步方向**：抽出不依赖 Web 应用初始化的服务装配入口，显式注入资源解析、事件输出及交互接口；Web 适配层继续使用 Flask。**不能用「整仓 Flask 关键词零命中」代替边界验收**；验收标准 = 真实服务在无 Flask 环境中完成初始化与任务生命周期（§7.2）。

---

## 4. 达成度矩阵（核心差距）

### 4.1 四层逐层对照（G5 口径修正）

| 层 | Roadmap 要求 | 现状 | 达成度 |
|---|---|---|---|
| **Client Layer** | Web/Desktop/CLI/IDE/Android/SDK | Web、Android WebView、React/Ink CLI 存在；**CLI 仍是「Web API 消费者」**（`cli/src/api.ts`：fetch `127.0.0.1:8091` + cookie/CSRF/hostLogin，并自启 `python -m server.app`） | ⚠️ 客户端存在，但全部消费 Web 专属业务流程 |
| **Stable Runtime Protocol** | Session/Turn/Item/Event 原语 + Commands/Events 契约 | **稳定公共契约不存在**。已有基础：`_append_event` 已注入 task_id/conversation_id/workspace_id 并分配任务内 idx（`models.py:757-782`）。真正缺的：稳定公共契约、**跨任务及非运行中状态变化的覆盖**、可查询快照与同步规则 | ❌ 未开始（有可用基础） |
| **Gateway** | 状态唯一 Owner；可独立初始化 | 进程内公共入口已建立（RuntimeService + 状态责任表）；但 **import 依赖链仍耦合 Flask**（§3.3），无法独立启动；状态持有仍分散在既有 manager（§5） | ⚠️ 入口收敛 ✅ / 独立初始化 ❌ |
| **Agent Runtime** | agent loop / context / workflow / sub-agent | 已存在且**运行期**请求上下文依赖已消除；但 §3.3 的模块依赖与输出耦合仍在，「达成」限定为进程内、Web 进程伴生形态 | ⚠️ 进程内达成 |
| **Execution Plane** | Host/Docker/Remote + 统一 Execution Contract | Host 沙箱（Seatbelt/bwrap/WSL2）+ Docker（非特权 uid + Landlock）已成熟；无统一 Execution Contract 抽象；Remote Worker 未做（P2） | ⚠️ 部分 |

> **口径说明（G5）**：薄入口、命名差异及端点数量**不能直接折算成四层边界完成比例**；本表「达成度」按各层职责（§1.2）能否独立履职衡量。

### 4.2 Phase B 验收清单逐项对照（§41）

| §41 Phase B 条目 | 状态 | 说明 |
|---|---|---|
| 定义 Session / Turn / Item / Event | ⚠️ 部分 | 概念对齐已做；Item 生命周期未定义；主 Run/子智能体/后台任务的映射未契约化 |
| 抽出 RuntimeService | ✅ | 已收敛全部 6 处入口（§3.2）；独立启动未达成（§3.3） |
| Flask route 只做 adapter | ⚠️ 部分 | 任务受理/控制链路（16 端点）已是 adapter；其余 180 端点保持 Flask 直写（评估结论：不需要迁移）；**审批回答端点仍直调 manager（见 §4.3）** |
| CLI 改为 Runtime Client 语义 | ❌ | 后置（§7.6） |
| 定义 event sequence / reconnect 模型 | ⚠️ 部分 | task 级 idx/offset + 前端对账去重已有；序号作用域/快照水位/续传衔接未定义 |
| 写 docs/runtime_protocol.md | ❌ | 现有 `runtime_contract.md` 是内部契约；协议文档（Entities/Commands/Events/错误码/版本）未写 |
| 为 protocol 生成 TS types | ❌ | 未做（启动条件：多客户端类型维护成实际成本） |

**小结：7 项中 1 项完成、3 项部分、3 项未做。** 「Web 与 CLI 使用同一套 Runtime contract，不需要知道彼此存在」尚未达到。

### 4.3 公共能力范围（G6）

「16 个端点」只是任务受理/控制的迁移估算。多端独立接入需要的公共能力应按**客户端操作**列清单，再确定所属服务：

> 工作区选择 · 会话创建/加载/历史 · 运行/取消/引导 · 审批与提问 · 模型及权限设置 · 必要附件能力 …

现状核查发现：**审批回答（`server/chat/approval.py`）仍直接调用 manager**，未经公共入口（契约 §4.2 明确审批回答不进 RuntimeService 首版）。旧 URL 可以保留，但这些业务能力**不能要求新客户端复制 Web 路由内的装配与编排**；与 Agent 使用无关的管理页面无需机械迁移。

---

## 5. 状态责任表 S1-S10 落地核查

> 全文见 `audit_state_ownership_v2/state_ownership_audit.md`。

| # | 状态 | 一致性 | 关键点 |
|---|---|---|---|
| S1 | 对话历史与元数据 | ✅ | merge-on-save + `_io_lock` + 原子替换 + 缩减拒绝（`crud_mixin.py:252-358`、`base.py:47`、`index_mixin.py:104`） |
| S2 | 任务记录 TaskRecord | ✅ | 唯一 TaskManager；chat 互斥 + notice 豁免（`models.py:158-173`）；纯内存 |
| S3 | 任务事件流 | ✅ | deque maxlen=20000、idx 单调、offset 续读（`models.py:74/757-782/200-206`）；纯内存 |
| S4 | 主任务门闸 | ⚠️ 基本一致 | 唯一入口/finally 释放/token 移交/失败回滚全部命中；**疑点 N1：异常回退路径（`chat_flow_task_main.py:644-690` 直跑 `handle_task_with_sender`）自身不释放门闸且无任务线程，回退场景门闸可能长期占用**（静态推断，待复现） |
| S5 | 停止标志 | ✅ | `server/state.py:38` 模块级 dict；REST 硬取消 vs socket 软标志；任务级键隔离；无锁全局可变（进程内依赖事件循环+线程隔离） |
| S6 | 审批/提问条目 | ✅（含缺口证实） | 三个内存 manager + 锁内单次裁决；契约 §6 四个缺口全部证实：超时不回写终态 / 无 TTL / **task_id 恒 None（静态证实：WebTerminal 全仓无赋值点）** / socket 软 stop 不打断等待（大概率） |
| S7 | 对话级 terminal | ✅ | 三段 key（`resources.py:52-54`）；回收器 24h + 实例身份校验（`reaper.py:40/92-130`） |
| S8 | 权限模式/执行环境 | ✅ | 运行中入队由工具循环消费（`permission.py` + `main_terminal.py:325-376` + `chat_flow_tool_loop.py:1544`） |
| S9 | 多智能体实例状态 | ✅ | `GLOBAL_MULTI_AGENT_STATES` 进程级单例 + RLock + 快照 + 终态校准（`multi_agent/state.py:668/671`、`sub_agent/state.py:104-133`） |
| S10 | 用户偏好 | ⚠️ 基本一致 | **新发现偏差：`save_personalization_config` 直接覆写 `open(path,"w")`，非原子写**（`personalization_manager.py:851-856`） |

**契约 §3 六项正确性保障**：代码中全部找到对应实现（第 6 项附 N1 疑点）。

### 5.1 关键判断：归属、共享、恢复是三件事（G7 口径改写）

v1 原文把「内存态」直接判为「状态唯一 Owner 的障碍」，混淆了三个不同问题，现拆开：

1. **状态归属（谁是权威）**：进程内达成度较高——每类状态基本都有唯一 owner 与互斥（9/10 一致或基本一致）。✅
2. **多客户端共享（§14 场景：CLI 发起、GUI 审批）**：**只需两个客户端连接同一个 Gateway 实例，内存中的任务与审批即可共享**。Gateway 是统一管理边界，内部可继续由 TaskManager、审批 manager、会话存储各自持有状态；关键是**授权、写入和裁决路径统一**。**内存态不构成此场景的障碍。**
3. **跨重启恢复**：这才是内存态（S2/S3/S6）真正限制的能力——重启后任务记录/事件流/审批条目即失。是否补齐由跨重启查询/审计需求决定（持久化被列为后续独立决策），**不能单凭未落盘判定「无唯一 Owner」**。

锁、原子写和丢更新风险应按真实写入并发分别处理（S5 无锁、S10 非原子写属此类，见 §6）；持久化本身也不会自动解决这些问题。

---

## 6. 已知缺口与遗留待办

### 6.1 必须先闭环的疑点

| # | 事项 | 口径（G8 修正后） | 来源 |
|---|---|---|---|
| N1 | S4 异常回退路径门闸释放 | **保持待复现**：不能仅因回退函数自身没有 release 就判定泄漏；验证应覆盖预占→受理失败→回退执行→最终释放全链路 | 当日 S4 核查 |
| N2 | 审批条目 task_id 恒 None | 静态证实。修复方向：**在审批创建处显式传入当前 Run 上下文**；不宜把「给会话级 terminal 加 task_id」直接确定为方案——若沿用可变属性，必须处理通知任务重叠、作用域恢复和清理。审批/计划确认/用户提问需保持各自语义，并补齐超时/取消终态与迟到回答规则 | 当日 S6 核查 + G2 |
| N3 | 真实运行环境验证未做 | Web 聊天/停止/审批/workflow 激活/多智能体派发需重启服务后人工完成；`get_user_resources` 分支选错会静默串工作区（最高风险点） | G1 |
| N4 | 并发重复任务记录 | `models.py:157`「检查运行中→创建记录」与门闸分属两处，并发下是否产生重复任务记录未验证 | G7①/G10 |
| N5 | `issue_socket_token` 发放竞争 | 未复现；若存在作为独立小缺陷处理 | G7② |

### 6.2 结构性缺口

- **跨重启恢复能力**：S2/S3/S6 纯内存态，重启即失；S6 条目永不清理（无 TTL）。TTL 应在终态与迟到回答规则明确后设计，不能直接删除仍合法等待的请求（G11）。
- **审批链路四个缺口**：超时不回写终态 / 无 TTL / task_id 恒 None（=N2）/ socket 软 stop 不打断等待（REST 硬取消可打断）；超时语义（拒绝当前工具继续 vs 结束任务）是产品决策未定，R5 明确其不是定时任务的技术前提。
- **S10 personalization 非原子写**：原子替换可防写中断损坏；但读改写并发的丢更新风险需另行判断（G8）。
- **独立启动缺口**：§3.3 的 import 依赖链（G4）。
- **事件前台干扰**：定时任务事件会推给在线用户 socket 房间，需 source 字段或产品确认（G13-old）。
- **异常回退路径绕过 TaskRecord 登记**：无事件 deque、不可按 task_id 轮询/取消（G14-old）。
- **阶段三定时任务未做**：详细设计要求见 work_plan §4（research_summary.md §1 全量保留）；本轮路线重排后**后置**（§7.6）。
- **后续独立决策**（有明确需求再启动）：SSE/WS 传输替换、对话级订阅、durable 事件与快照恢复、审批持久化、TS/SDK 生成、身份体系整理（不能直接合并 Web/API 用户数据空间）、设备配对/Remote Worker。
- 存量测试失败 4 项（与改造无关，未修）。

---

## 7. 后续路线（按 G9 重排为五步 + 前置第 0 步）

> 总原则：协议草案与接口适配可迭代推进；**每一步保留现有门闸、保存和恢复保障**；四层不要求四进程（G13）；Remote Worker 不是本轮完成条件。

### 7.1 第 0 步：闭环疑点与小修复（不依赖架构决策）

1. 复现验证 N1（回退路径门闸，覆盖预占/受理失败/回退执行/最终释放全链路）。
2. 修复 N2：审批创建处显式传入当前 Run 上下文（方案比选后实施，见 §6.1）。
3. 修复 S10：`save_personalization_config` 改用原子替换写。
4. 完成 N3 真实环境验证清单。

### 7.2 第 1 步：独立服务启动（Gateway 可脱离 Web 初始化）

- 拆解 §3.3 的 import 依赖链：`server/tasks/__init__.py` 的 Blueprint 创建与级联路由 import、`models.py → chat_flow` 的静态引用。
- 抽出**不依赖 Web 应用初始化的服务装配入口**，显式注入资源解析、事件输出与交互接口；Web 适配层继续用 Flask。
- **验收**：不启动 Web 应用也能创建会话、运行受控任务、查询并取消——以真实执行装配验证，不接受替换执行线程的替身测试（G13）。

### 7.3 第 2 步：完整公共协议 + 极简客户端验收

- 写 `docs/runtime_protocol.md`（§38）：Entities/Commands/Queries/Events + **授权范围、错误码、请求重试、兼容版本策略**（G3）。
- 能力清单按客户端操作划分（§4.3），审批回答等直调 manager 的路径纳入公共入口。
- **用非 Web 的极简测试客户端提前参与验收**（G12）：验证 工作区→会话→运行→审批/提问→停止→历史 全链路。
- 传输选择（G12）：**HTTP 可以承载公共 Gateway 协议，本地 socket 也可以——摆脱的是旧 Web 专属的登录、会话装配及路由业务流程，不是 HTTP 这个传输**。新客户端不依赖 Web Cookie/CSRF/hostLogin，但必须经过适合通道的认证与资源授权，**不能自报可信身份或内部门闸 token**。采用 stdio 时须区分连接适配器与服务实例——**不能让每个客户端各启动一份独立状态的 Runtime 却宣称共享 Gateway**。跨凭证访问同一资源需明确身份映射，不直接合并现有 Web/API 数据空间。

### 7.4 第 3 步：多客户端共享与恢复

- 场景验收：A 发起、B 查询并操作；A 断开不结束任务；重连后状态一致。
- **事件模型定义（G10）**：先定义序号作用域（会话或其他明确作用域即可，**不要求跨所有用户/会话的全局总序**）、事件覆盖、授权订阅、缺口检测、快照水位与续传衔接；保留现有任务 idx 时，须区分它与连接序号及其他游标的含义，并定义窗口过期后的重新同步规则。
- **重启语义三场景分开（G11）**：客户端断开 ≠ Gateway 重启 ≠ 原执行继续。多端接入首先要求任务独立于连接；Gateway 重启后旧 Run 的查询与失效规则须明确；若保留旧 Run 记录，恢复为中断/待核验等明确状态，**不无依据宣告成功、不自动重做可能已有副作用的动作**。持久化 pending 审批不会重建原执行线程；只有另行实现待执行动作恢复、权限重校验及结果对账后，才能承诺重启续跑。
- 持久化范围由跨重启查询/审计需求决定；当前内容快照与已提交生命周期记录可分别设计，**live delta 无需默认全部持久化**；实现仍须遵守原 work plan 的一致提交、快照水位、裁剪与恢复规则。

### 7.5 第 4 步：Runtime 与执行环境分层

- 定义 Execution Contract：相同 Runtime 可接测试执行器（替身）及现有 Host/Docker 实现；执行链无需了解 Web 会话与客户端连接。
- 验收：Runtime + 替身执行器可独立测试；Host/Docker 后端经同一契约接入。

### 7.6 第 5 步：正式客户端接入 + 定时任务后置接入

- Web 与正式 CLI/GUI/IDE 接入同一业务服务与协议。
- **定时任务（原阶段三）后置接入该边界**，不作为公共协议建设的前置条件（G9）——按 work_plan §4 设计实施（幂等/重叠/停机/审批超时/持久化选型）。

### 7.7 本轮 Gateway 化的完成判据（G13，行为验收）

1. 新客户端通过**公开承诺的协议能力**完成 Agent 使用全流程，无需复刻 Web 启动逻辑。
2. Web 与其他端对同一会话/Run/审批状态一致。
3. 断连不终止已受理任务；重连与 Gateway 重启分别有明确处理规则。
4. 所有任务来源遵守统一受理、取消、授权、保存及事件规则。
5. Gateway 可独立初始化；Runtime 与执行环境可通过替身独立测试。
6. **测试必须覆盖真实执行装配与生命周期**——不能以替换整个执行线程后的入口测试通过认定完成。

### 7.8 不在路线内（Roadmap §42 明确反对）

- 拆微服务 / 引入 K8s；重写前后端；每个 sub-agent 独立进程；把四档权限 UI 改成 policy DSL 暴露给普通用户。

---

## 8. 附录

### 8.1 本次核查产物索引

| 产物 | 路径 |
|---|---|
| 任务入口收敛核查（6 处迁移点 + 漏网扫描 + Flask 拆桥） | `cache_research/gateway/audit_entry_points_v2/entry_points_audit.md` |
| 状态责任表 S1-S10 落地核查（含 N1/N2/S10 新发现） | `cache_research/gateway/audit_state_ownership_v2/state_ownership_audit.md` |
| 既有研究汇总（三阶段要点 / R1-R6 / 早期盘点修正对照 / G1-G18 遗留全集 / 外部借鉴） | `cache_research/gateway/audit_research_summary_v2/research_summary.md` |
| 内部契约 | `docs/runtime_contract.md` |
| 三阶段工作计划 | `cache_research/gateway/gateway_work_plan.md` |
| 阶段一/二实施与验收记录 | `cache_research/gateway/phase12_implementation_plan.md` |

### 8.2 审阅批注消化对照表（G1-G13 → 本版落点）

| 批注 | 核心意见 | 本版落点 |
|---|---|---|
| G1 本轮目标 | 目标重锚为四层+多端独立接入，优先级调整 | 文首锚定 + §7 全线重排 |
| G2 四层职责 | 四层职责明确划分；Gateway 内部可委托多 manager | §1.2 |
| G3 协议演进 | Turn 命名不是缺口；Item 逐步适配；协议需 Commands/Queries/Events/授权/错误码/重试/版本 | §1.4、§7.3 |
| G4 独立启动缺口 | import 依赖链仍耦合 Flask；运行期显式化 ≠ 加载期解耦 | §3.3（含复验证据）、§7.2 |
| G5 达成度口径 | _append_event 已注入关联字段；薄入口/命名/端点数不折算完成比例 | §4.1 |
| G6 公共能力范围 | 16 端点非能力上限；按客户端操作列能力清单；审批回答仍直调 manager | §4.3 |
| G7 Owner 与恢复分开 | 归属/共享/恢复三件事分开；内存态不构成多端共享障碍 | §5.1 |
| G8 缺陷验证口径 | N1 保持待复现；N2 修复走显式 Run 上下文；S10 丢更新另行判断 | §6.1、§6.2 |
| G9 路线重排 | 五步路线；定时任务后置 | §7 |
| G10 事件持久化边界 | 序号作用域先行；非全局总序；live delta 无需默认持久化 | §7.4 |
| G11 重启语义 | 断开/重启/执行继续三场景分开；持久化 pending ≠ 续跑；TTL 后设 | §7.4、§6.2 |
| G12 传输与客户端验证 | 摆脱 Web 专属流程而非 HTTP；stdio 防独立 Runtime 陷阱；极简客户端提前验收 | §7.3 |
| G13 完成判据 | 行为验收六条；测试须覆盖真实执行装配 | §7.7、§2.1 |

> 注：本文所有「文件:行号」证据为 2026-09-07 当日代码快照，后续变动以符号/函数名为准。
