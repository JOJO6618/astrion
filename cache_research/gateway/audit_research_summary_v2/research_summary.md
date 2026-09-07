# Gateway 既有研究文档汇总（research_summary）

> 生成日期：2026-09-07
> 任务性质：**全程只读**，未修改任何项目文件（仅在交付目录生成本汇总）。
> 汇总者：子智能体 #6。
> 汇总原则：不掺入自身推测，所有结论注明来源文件；文档间有冲突时并列呈现并注明。

## 来源文件清单（实际读取的文件）

| 优先级 | 任务指定 | 实际路径（已确认） | 状态 |
|---|---|---|---|
| 1 | `cache_research/gateway/gateway_work_plan.md` | `cache_research/gateway/gateway_work_plan.md`（2026-09-07 修订版） | ✅ 已读 |
| 2 | `cache_research/gateway/phase12_implementation_plan.md` | `cache_research/gateway/phase12_implementation_plan.md` | ✅ 已读 |
| 3 | `cache_research/gateway/eval_summary.md` | `cache_research/gateway/eval_summary.md`（含 R1-R6 审阅注释） | ✅ 已读 |
| 4 | 四份评估子报告 | `cache_research/gateway/eval_task_entry_points/task_entry_points.md` | ✅ 已读 |
| | | `cache_research/gateway/eval_flask_context_deps/flask_context_deps.md` | ✅ 已读 |
| | | `cache_research/gateway/eval_endpoint_classification/endpoint_classification.md` | ✅ 已读 |
| | | `cache_research/gateway/eval_event_approval_coupling/support_chains_coupling.md` | ✅ 已读 |
| 5 | `astrion_audit/astrion_gateway_gap.md` | `cache_research/gateway/astrion_audit/astrion_gateway_gap.md`（**实际位于此路径**，工作区根无 `astrion_audit/` 目录，见下方说明） | ✅ 已读 |
| 6 | 外部参考 | `cache_research/gateway/opencode_study/opencode_architecture.md` | ✅ 已读 |
| | | `cache_research/gateway/openclaw_study/openclaw_gateway.md` | ✅ 已读 |

**路径说明**：任务指定 #5 为 `astrion_audit/astrion_gateway_gap.md`，但工作区根目录不存在 `astrion_audit/`；实测唯一一份 `astrion_gateway_gap.md` 位于 `cache_research/gateway/astrion_audit/astrion_gateway_gap.md`（已被 gateway 相关文档同置于 `cache_research/gateway/` 下），本汇总即基于该文件。已按要求**未读取** `audit_entry_points_v2/` 等其他目录（属他人产出）。外部参考只需提炼被本项目借鉴的设计点，未做全文复述。

---

## 1. 三阶段路线全文要点（来源：gateway_work_plan.md，2026-09-07 修订版）

**总体目标**：让 Astrion 的内部职责、状态修改规则和任务入口更清晰，使后续功能沿稳定边界扩展。近期明确新功能场景 = **定时任务**。长期方向是 Gateway（Runtime/Gateway），近期交付是在**现有进程内**整理运行时服务边界，让 Web、CLI 和定时触发器复用同一套任务受理/执行/控制逻辑。验收依据 = 新增入口所需理解和修改的范围 + 现有行为是否保留。

**近期主线（三件事）**：
1. 固定已有状态边界和正确性保障。
2. 抽出接收显式运行上下文的公共任务入口。
3. 通过定时任务验证该入口，并补齐调度所需的持久记录和生命周期。

**非本次前置条件**：事件持久化、传输替换、公开 SDK、设备配对和远程执行，分别评估。

**参考资料边界（work plan 原文）**：
- 原始愿景 `.astrion/user_upload/Astrion_Architecture_Product_Roadmap_Review_1.md`（重点 §10–14、§35、§37–38）。
- 原始盘点 `astrion_audit/astrion_gateway_gap.md` 的"无唯一 owner""补丁不是投影""恢复机制脆弱"等结论**须结合新核查理解，不能直接当作未修复故障**。
- 外部研究只参考职责分离/契约/幂等/恢复设计，不复制其部署/存储/认证方案。

### 现状关键结论（work plan §1，静态代码分析）
- **已有保障**：
  - 对话级资源隔离：`server/context.py:58` 按 username/workspace/conversation 生成终端 key。
  - 主任务门闸：`server/chat_flow.py:139` 获取、`:258` finally 释放，实现于 `server/main_task_gate.py`。
  - 对话保存保护：`utils/conversation_manager/crud_mixin.py:335` 按 message_id 合并防缩减；`:194` I/O 锁；`index_mixin.py:104` 原子替换文件。
  - 客户端恢复：`static/src/stores/task.ts:156` 偏移轮询；`probe.ts:73` 对账恢复；`lifecycle.ts:137` 按 task_id/idx 去重。
  - 审批单次决定：`modules/tool_approval_manager.py:65` 锁内裁决 pending，已决定时返回现状。
- **耦合**：
  - 执行依赖 Web 环境：`server/tasks/models.py:785` 后台任务用 test_request_context + 回填 session 获取资源执行。
  - 写入口分散：任务 manager、terminal、conversation manager 各有职责和直接调用方。
  - 过程记录为内存态：`models.py:75` 有界事件 deque、`:108` 清理旧任务、`:762` 分配任务级 idx；审批 manager 为进程内实例。
- **优化候选**：`server/context.py:75` 用户级广播（当前也是投影方案）。
- **新能力（定时任务）**：本轮搜索只发现任务清理、idle reaper 等维护定时器，**未发现用户 Schedule 实体与到期派发链路** → 需单独设计调度状态与记录，复用现有任务执行。
- **两个有界核验项**（勿将静态推断升级为故障）：
  1. `models.py:157`"检查运行中任务→创建记录"与最终执行门闸分属两处 → 核验并发是否产生重复任务记录。
  2. 原报告提到 `server/chat/terminal.py::issue_socket_token` 发放竞争，本轮未复现；若仍存在作为独立小缺陷处理。

### 阶段一：固定契约与已有不变量（目标/范围/验收）
**目标**：明确状态属于谁、新入口应该调用哪里。
**范围清单**：
- [ ] 产出 `docs/runtime_contract.md`（先描述内部服务契约；暂不强制公开网络协议/握手/实体全面改名）。
- [ ] 建立状态责任表：每项列明权威来源、允许的修改方、持久化入口、缓存刷新、并发裁决、失效条件。内存/文件/客户端缓存可共存，**权威关系必须明确**。
- [ ] 对齐概念：**Session=现有 conversation，Run=一轮主任务，Schedule=计划，Occurrence=某次到期触发，Event=变化通知**。子 agent、后台命令与主任务的关系单独说明，不把现有 task 全部等同主 Run。
- [ ] 审批与用户提问保留不同语义；可共享 ID/关联/等待/回答基础设施，**提问答案不能被当作工具执行授权**。
- [ ] 明确公共入口所需 principal、workspace、conversation、模型/运行配置与事件输出接口；默认值由明确解析步骤产生。
- [ ] 建立调用方迁移表：Web/CLI 对应 API、Workflow 激活、通知派发、定时触发；每项标注上下文来源、门闸获取/释放、取消传播。
- [ ] 围绕修改范围保留/补充回归用例：同对话并发、不同对话隔离、保存不丢消息、取消、审批重复回答、偏移恢复、过期响应过滤。
**完成标准**：目标入口的状态修改路径和执行裁决可定位；已有保障成为明确约束，未验证风险有独立记录。

### 阶段二：抽出显式上下文与公共任务入口（目标/范围/验收）
**目标**：无浏览器也能通过受控入口启动、观察和停止一轮任务。
**架构示意**：
```text
Web / CLI 适配层       定时触发器       Workflow / 通知派发
        \                 |                 /
             公共任务受理与控制入口
                       |
             现有执行门闸与 Agent 执行
                       |
          现有保存、审批、事件和取消链路
```
**范围清单**：
- [ ] 以 `TaskManager.create_chat_task` 及执行链路为基础抽出服务接口（`RuntimeService` 为名称候选；文件位置按真实职责确定，避免把所有 manager/状态塞入新类）。
- [ ] 服务接口至少覆盖提交、查询、取消；审批回答复用现有 manager 通过明确关联接入；会话创建按需复用，第一条验证链路可用已有会话。
- [ ] HTTP 参数解析、Cookie/CSRF/Bearer 验证放适配层，向服务层传可信 principal 和经校验资源范围；**不能接受客户端或 Schedule payload 自报 role 作为授权依据**。
- [ ] 消除目标执行链路对隐式 Flask session 的读取；迁移期可保留兼容适配层但要列出剩余依赖；**只把 test_request_context 包进新方法不算完成解耦**。
- [ ] 复用门闸/取消/审批/保存规则；分别定义**任务受理去重**与**实际执行互斥**，防重复启动或门闸泄漏。
- [ ] Web 任务入口先接入服务，再逐条迁移 Workflow/通知等调用方；每次明确哪些旧写路径已封闭。**不能把"门面转发成功"写成"全部状态唯一 Owner 已完成"**。
- [ ] 内部错误采用稳定状态/错误码；HTTP 状态码由适配层映射；事件先适配现有 idx/offset 和 sender，不同时重写客户端。
**完成标准**：不创建浏览器会话、不伪造 HTTP 请求，测试能通过显式身份和资源上下文启动一次受控任务、读取结果并取消；同对话重入仍受门闸保护；现有 Web/CLI 行为兼容。执行可用可控模型/工具替身验证，无需真实外部副作用。

### 阶段三：定时任务纵向落地（目标/详细设计要求——本节最重要）
**目标**：时钟成为公共任务入口的另一个调用方。本节为**待实施设计**（不代表当前已有调度器），下列保守默认作为讨论起点，UI 与默认行为在实现前确认。

#### 4.1 计划与触发记录
- Schedule 至少保存：ID、所属 principal、目标 workspace、会话策略、提示词/任务配置、时间规则、时区、启用状态、配置版本。**明确夏令时重复/不存在时刻的处理**。
- 支持创建、暂停、恢复、删除计划。**建议暂停/删除只影响未来触发，已受理的 Run 另行取消**；最终行为须明确。
- 明确使用已有会话还是每次新建会话；首版可只实现一种，但须说明**上下文累积、目标删除和工作区失效时的行为**。
- 模型配置确定"创建时固定"还是"触发时解析"；**权限在触发时按当前有效授权重新校验，不保存可绕过权限变更的长期授权快照**。
- 每个 Occurrence 有**稳定触发标识**（例如 schedule_id + 计划时间点）；保存所用配置版本、受理状态、run_id 和终态。计划编辑后的未来触发身份规则须明确。

#### 4.2 重复、重叠与停机
- 为 Occurrence 登记和 Run 受理定义**持久幂等规则**：同一触发重试返回同一受理结果，**相同键不同参数拒绝**；记录保留期覆盖允许的重试窗口。
- 明确"登记后未启动""已启动但关联未写完"等**崩溃窗口**。登记/受理应**原子提交或具备可验证的恢复对账**；不能只用内存 TTL 承诺跨重启不重复执行。
- 选定**单个活动调度器**的启动与所有权规则，防重载器或多个服务进程重复派发；不要求因此引入分布式基础设施。
- 同会话已有任务或上一轮仍运行时，定义 **skip/queue/parallel** 策略。**建议首版跳过并记录原因**，沿用会话门闸，不默默增加无限队列。
- 定义服务关闭期间错过触发的策略。**建议首版记录错过并等待下个未来时点，不集中补发**；服务需运行才会触发，本阶段不包含 OS 唤醒/开机自启。
- 重启后恢复 Schedule 和触发记录；**上一进程未确认完成的 Run 标为中断或结果待核验，不显示仍在正常运行，不自动重做可能已产生副作用的动作**。
- 触发去重仅保证任务受理规则，**不承诺任意外部工具副作用 exactly-once**。无法确认的执行结果进入显式待核验状态。

#### 4.3 审批、存储与验收
- 无人在线时仍保持原有权限限制：**遇到审批/提问按明确超时等待，超时结束或中断本轮并记录原因，不自动扩大权限**；等待中的任务也遵守重叠策略。
- 审批关联当前 Run/会话，复用现有 pending/answer 链路。**重启后旧请求不能被当作仍有执行现场的有效审批**；持久审批记录与继续执行分别设计。
- 为 Schedule/Occurrence/必要的运行摘要选持久化方案，**先列出原子更新、唯一性、查询和恢复要求，再决定文件或 SQLite**。数据走运行态路径解析，不写源码树；不强制迁移全部对话历史。
- 沿用当前任务事件读取；补充触发记录查询，使旧内存任务被清理或重启后仍能解释触发结果。日志记录 schedule_id/occurrence_id/run_id，复用既有设施。
**完成标准**：可控时钟与执行替身验证一次触发、重复触发、任务重叠、计划暂停/恢复、目标失效、无人审批超时、停机错过触发及重启对账；真实入口验证不依赖浏览器。**明确只恢复计划与记录，不承诺从任意执行位置续跑**。

### 后续独立决策（有明确需求再启动）
| 决策 | 启动条件 | 决策前必须补充内容 |
|---|---|---|
| SSE / WS / 保持轮询 | 延迟/连接数/带宽不满足，或新增双向交互 | worker 模型、代理缓冲、重连、慢消费者；传输更换≠状态正确性提升 |
| 对话级订阅 | 需减少无关广播、精确受众 | 订阅与资源授权分别校验，区分任务/会话事件与用户级通知 |
| durable 事件与快照恢复 | 需超出内存窗口的过程追溯/跨重启生命周期查询 | 提交时机、序号作用域、快照边界、日志裁剪、缺口处理、schema 版本 |
| 审批持久化与可恢复执行 | 需重启后继续等待并执行原动作 | 重建上下文与待执行动作、权限重校验、过期请求处理、结果不确定性；恢复 pending 记录不能实现续跑 |
| TS 类型 / SDK 生成 | 对外契约或多客户端类型维护成实际成本 | 单一 schema 权威源、兼容策略、生成检查；不强制新网络握手 |
| 身份体系整理 | 跨凭证访问同一资源或统一授权成需求 | 统一 principal/resource/authorization；各适配层可保留不同凭证，不能直接合并 Web/API 用户数据空间 |
| 设备配对 / Remote Worker | 明确需多设备接入或远程执行 | 执行契约、连接身份、所有权转移、故障语义，独立设计验收 |

**事件恢复改造五条硬边界**（若启动）：
1. 快照注明覆盖的事件水位 N 并与同一状态版本一致；续传从 N 之后开始；快照生成/订阅/历史读取之间不得漏事件窗口；重复投递仍需幂等应用。
2. durable 事件只承诺已提交记录可恢复；live delta 是否恢复单独定义；运行中未完成 Item 需当前内容快照/覆盖式更新/不完整标记，不能只依赖最终 completed 事件。
3. 状态与事件写入需一致提交/恢复规则；对话 JSON、事件 JSONL、审批 JSONL 不天然构成一致快照与日志。
4. 连接序号、任务偏移、持久会话序号不能混用；连接重建/历史裁剪/会话重置时给出明确重新同步规则。
5. JSONL 与 SQLite 都需定义恢复、保留、迁移方案；选择依据是事务与查询边界，不承诺以后可低成本平移。

### 改造完成判断（work plan §6）
- 新增任务来源只需构造显式上下文、校验目标并调用公共入口，无需复制 Web 聊天启动流程。
- 已迁移路径有明确状态权威与写入规则，现有门闸、保存、客户端恢复保护保留。
- 定时任务有可查询的计划、触发记录和终态；重复、重叠、权限变化与停机行为可解释。
- 各阶段可独立验证；只为当前边界迁移做必要接口调整，不同时重写 Agent loop、前端状态管理或部署拓扑。
- 每阶段说明实际迁移入口、剩余兼容依赖、验证结果。**完成门面、生成架构图或更换传输本身不算完成改造**。

---

## 2. 范围评估核心结论（来源：eval_summary.md + 四份子报告，含 R1-R6 审阅修订）

### 2.1 196 个端点分类结果（来源：eval_endpoint_classification/endpoint_classification.md；经 R1/R2 修订）
统计口径：以 Flask 路由注册（Methods 合并计数）为准，共 **196** 个，分 26 个文件，与给定分布一致。

| 类别 | 数量 | 占比 | 是否迁移 |
|---|---|---|---|
| T1 任务受理 | 3 | 1.5% | ✅ 必须（POST /api/tasks、/api/v1/.../messages、/api/workflow/activate） |
| T2 任务控制 | 13 | 6.6% | ✅ 必须（cancel×2、runtime_guidance/queue×4、workflow deactivate、sub_agents/background 停止×3、审批回答×3） |
| T3 任务观察 | 14 | 7.1% | ⚠️ 可不动（只读 REST，轮询协议天然可复用） |
| C CRUD | 131 | 66.8% | ❌ 不动 |
| S 状态查询 | 20 | 10.2% | ❌ 不动 |
| A 认证管理 | 15 | 7.7% | ❌ 不动 |
| **合计** | **196** | 100% | — |

**核心答案：必须迁移的最低集合 = T1 + T2 = 16 个端点（8.2%）**。16 个端点全部收敛到同一批 manager 单例方法（task_manager / sub_agent_manager / background_command_manager / 三个 approval manager）。**Gateway 化实质 = 把这几个 manager 方法提升为 RuntimeService 公共入口**，HTTP 端点从"直接调 manager"改为"调 RuntimeService"，而非改写端点本身。

**迁移复杂度分布（16 个）**：**12 小 / 3 中 / 1 大**（唯一大项 = workflow activate，因门闸 token 移交 + 状态机编排）。

**审阅修订要点**：
- **R1（范围）**：16 个是当前分类下的受理/控制端点集合，**不是架构改造完成的充分条件**。`server/chat/permission.py` 的权限/执行环境/网络权限变更在任务运行期会排队生效，**必须进入阶段一状态责任表**；其余端点应描述为"多数可保持 HTTP 兼容"，不能概括为"与运行时零耦合"。查询端点可保留原 URL，但后台调用方（CLI/定时任务）应能通过内部接口（如 `get_task_events`）查询，不必为观察任务发 HTTP。**新增门面不等于全部写入口已收敛**。对应子报告 §4.4 明确：`chat/permission.py:166-185 / :265-284 / :352-368` 分别排队修改运行中的权限/执行环境/网络权限，`work-mode` 在 `:420-445` 拒绝运行中切换。
- **R2（计数与编排）**：11+3+1=15 应为 **12 小 / 3 中 / 1 大 = 16**，仍为定性估算。Workflow 编排（会话补建、激活、门闸移交、失败回滚）留在 HTTP 层只适合作为**过渡**，长期应按复用需求下沉到工作流服务供非 HTTP 入口调用；无需全部塞入 RuntimeService。
- **R3（依赖与异常路径）**：core/modules/utils 无直接 Flask 导入已确认，但**不能外推为所有间接调用都不依赖请求上下文**，"无旁路"须限定为正常受理路径：`chat_flow_task_main.py:645-675` 在任务创建失败后直接执行 `handle_task_with_sender`，**绕过 TaskRecord 登记**（无事件 deque、不可按 task_id 轮询/取消）；外围轮询器有预占门闸，不能仅凭回退认定并发写入 bug，但**迁移验收必须覆盖回退路径的记录、事件、取消、门闸生命周期**。socket `send_message` 在 `socket_handlers.py:259` 已短路返回 DEPRECATED，属死代码，应与活跃回退分开。"5 个调用点"实为 5 类来源、6 处调用位置。
- **R4（上下文设计与工作量）**：9 字段是旧 session 依赖的搬迁清单，不是最终领域模型；应区分"可信身份与资源范围""本次任务参数""内部执行信息"（门闸 token/通知回滚**不得成为普通客户端可提交字段**）；明确默认值/对话配置/本次覆盖的解析优先级。6-8 文件属估算；`get_user_resources` 有约 **170+ 调用处**（含 83 处 @with_terminal 装饰器路径），影响面需 host/Web/API 身份 × 会话 × 默认值来源回归覆盖，不宜承诺每处都小。
- **R5（审批产品边界）**：拒绝当前工具后继续运行**不自动意味着不安全**（后续动作仍受权限约束）。等待人工/到期终止/拒绝当前动作后继续是不同产品策略，**不能把"定时任务禁止人工审批"当作必选技术条件**。工具审批、计划审批、用户提问应分别定义超时含义。超时策略可在阶段三明确，**不阻塞**保持原有语义的阶段二上下文重构。
- **R6（取消与生命周期）**：标准停止按钮走 **REST 硬取消**，可打断审批等待；缺口应**限定为仅设置软停止标志的路径**（socket stop_task 软 stop），不能描述成所有停止按钮失效。需跟进超时/取消后 pending 终态更新、审批与 Run 关联；条清理不能只按 TTL 删仍有合法等待者的请求。task_id 恒 None 目前仍是**静态疑点**，须验证运行时载荷后定性。

### 2.2 任务链路 Flask 依赖性质（来源：eval_flask_context_deps/flask_context_deps.md；经 R3 修订）
**总体判定：依赖属于「浅层入口型」，集中两个枢纽函数，执行体干净。**
- **出口即枢纽**：
  1. 入口 `server/tasks/models.py::create_chat_task`（直读 session 快照）+ `_run_chat_task`（test_request_context 包裹后台线程再调 get_user_resources）。
  2. 资源获取 `server/context.py::get_user_resources`（及内部 `_apply_workspace_personalization_preferences`、admin policy）。
- **执行体干净**：`core/`、`modules/`、`utils/` **零 Flask 隐式上下文**（`session` 同名变量全是终端会话/容器句柄或 dict 参数，非 Flask）；`server/chat_flow*.py` 9 个文件函数体内**零** session/request/has_request_context 使用（只有 import 行）。
- 命中统计：测试 request_context 全仓仅 1 处（models.py:794）；has_request_context ~17 处全在 context.py；B 类（必须消除）集中在 2 文件：tasks/models.py（session 15 行）+ context.py（session 31 行）。
- **B 类依赖清单**（必须消除/显式化）：
  - **B-1** `models.py:785-812` `_run_chat_task` 的 test_request_context 包装（核心）；难点不在删 wrapper 而在 get_user_resources 参数化。
  - **B-2** `models.py:180-203` `create_chat_task` 直接读 Flask session（含 else 分支全量直读 http 上下文、setdefault 兜底，见下方"冲突/补充"）。
  - **B-3** `context.py::get_user_resources`（L221-535，核心）内部 8-10 处 has_request_context 守卫读写 host_mode/workspace_id/host_workspace_id/run_mode/thinking_mode/model_key/is_api_user + 无守卫 record/role 读。
  - **B-4** `context.py::_apply_workspace_personalization_preferences`（L116-178，读 model_key/回写）。
  - **B-5** `context.py::ensure_conversation_loaded`（L750-810，写 session 回写，任务线程内已天然跳过但属隐式耦合）。
  - **B-6** `server/auth_helpers.py:35-49` 认证辅助被任务链路经 get_user_resources 无守卫调用。
- **C 类均已核查归 A（可保留）**：`server/conversation.py` 的 host_mode/input_draft/terminal 相关函数、`with_terminal` 装饰器（83 处全部路由）、socket_handlers（flask-socketio 适配层）。
- 依赖深度结论：**浅层**。B 类无条件耦合仅 2 处（创建入口 + 线程包装），守卫型耦合集中在 context.py 一个文件。
- **R3 修订**：直接搜索不能证明所有间接调用都无上下文依赖；"浅层"描述依赖位置，不等于改动风险低；get_user_resources 的身份/资源分支需行为验证，不能以删除 import 或零关键词命中代替验收。

### 2.3 任务入口五点评估（来源：eval_task_entry_points/task_entry_points.md；经 R3 修订）
- 执行链（正常受理路径）已单一收敛：5 类来源、6 处调用位置共用 `create_chat_task → 线程 → _run_chat_task → run_chat_task_sync → process_message_task → asyncio loop.create_task(handle_task_with_sender)`。
- 六处调用点概览：

| # | 文件:行 | 入口 | 触发来源 | 显式化程度 | 迁移难度 |
|---|---|---|---|---|---|
| ① | tasks/api.py:200 | create_task_api | Web HTTP POST | 低（无 session_data） | 小～中 |
| ② | api_v1.py:320 | send_message_api | Web HTTP POST（Bearer） | 低（无 session_data） | **中**（is_api_user/role 必须显式） |
| ③a | workflow_runtime_api.py:184 | api_activate_workflow | Web HTTP POST | 高（session_data 显式） | 小～中 |
| ③b | workflow_runtime_api.py:264 | api_deactivate_workflow | Web HTTP POST | 高（同上，+通知池/门闸回滚） | 小～中 |
| ④ | chat_flow_task_main.py:613 | _dispatch_completion_user_notice | 内部后台轮询线程 | 极高（全显式） | **小** |
| ⑤ | chat_flow_task_main.py:1416 | _dispatch_multi_agent_idle_messages | 内部后台轮询线程 | 极高（全显式 + task_type=notice） | **小** |

- **迁移真实难点**（从大到小）：
  1. `get_user_resources` 的 session 隐式读取参数化（host_mode/is_api_user 分支选错即静默串工作区，最高风险）。
  2. API 身份字段（is_api_user/role）正确传递（调用点②，唯一无 session_data 的 API 来源）。
  3. main_task_gate 门闸移交语义（预占→token 随 session_data 移交→线程认领→finally 释放/失败回滚）。
  4. `task_type="notice"` 互斥豁免与单对话互斥语义保持（调用点⑤）。
  5. 三套身份取数来源统一（web session / token session / web_terminal 属性）。
- 另项（不在本 5 点但相关）：`socket_handlers.py:345 → start_chat_task`（socket 直连执行链，**不可达死代码**）；`chat_flow_task_main.py:664` 通知回退直接执行（活跃，见 R3）。

### 2.4 四支撑链路评估（来源：eval_event_approval_coupling/support_chains_coupling.md；经 R5/R6 修订）
总体判定：**事件/取消/保存三条可直接复用（需适配层）；审批链路需改造**。
- **事件链路：直接复用**。载体 TaskRecord（deque maxlen=20000 + 每任务 idx），调用方只需 create_chat_task 拿 task_id 即可轮询/取消；socket 推送按 `user_{username}` 房间（离线无影响，但用户在线会收到定时任务事件 → **前台干扰**，需加 source 字段或确认产品预期）。适配点：消除 test_request_context、定义定时事件来源标识。
- **取消链路：直接复用**。寻址=username+task_id（stop_flags 任务级 key=task_id=client_sid），不依赖 terminal（conversation 仅副作用）；保留"独立事件循环 + entry 持 loop/task"模式以支持硬取消；公共入口须把 task_id 持久化到 Occurrence。
- **保存链路：直接复用，前提明确**。写入口收敛于 `context_manager.add_conversation`（每消息 auto_save）；merge-on-save + `_io_lock`（manager 实例级，跨实例不互斥）+ 主任务门闸三道防线。前提：新调用方必须(1)走对话级 terminal、(2)遵守主任务门闸、(3)会话策略决定 conversation_id。存在执行链之外第二批写者（设置/压缩/CLI），共用同一保护。
- **审批链路：需改造**。核心问题（§2.4）：
  1. 等待循环默认超时 3600s 且调用点（:435/:574/:883/:1153）未传 timeout 参数（均有参数但调用点没传）。
  2. 超时语义现状=**拒绝该工具、任务继续**（非结束任务）——产品决策项，两种候选：(a)拒绝该工具继续（现状，风险无监督继续）；(b)结束整个任务。
  3. 等待期间软停止无效（下一工具调用行首 :600 才生效）。
  4. manager 条目无 TTL/清理。
  5. 存储 manager 可复用（与 Web 前端共用 pending/answer 数据）；`auto_approval` 分支（ApprovalAgent）是唯一现成无人工决策路径，但仅覆盖 tool 审批。
  - 候选改造（最小改动排序）：注入可配置超时（session_data 透传）→ 定义超时语义 → 等待循环加 stop 检查 → manager 条目 TTL/清理 → 会话策略决定审批是否可达。
- **R5 修订**：§2.4 的"禁止人工审批 / 60-300s 超时"均为**待讨论选项，不是定时任务的技术前提**；拒绝当前工具后继续仍受权限限制，不自动等于不安全；可等用户上线回答。工具审批、计划审批、用户提问应分别确定超时处理。客户端离线但服务运行时可保留现有 pending；跨服务重启保留记录与恢复等待执行是另外两层需求。
- **R6 修订**：停止按钮无效应限定为只置软停止标志的路径（标准按钮走 REST 硬取消可打断）；条目生命周期应先定义超时/取消终态与迟到回答处理，再设保留期清理，不能只按 TTL 删仍被等待的 pending；task_id 恒 None 保持静态疑点，待运行时关联验证。

### 2.5 审阅修订要点汇总（R1-R6 已嵌入各节）
见上述各节 R1/R2/R3/R4/R5/R6 处。

---

## 3. 阶段一/二实施与验收记录（来源：phase12_implementation_plan.md；另见项目记忆 gateway_runtime_work_plan）

**状态**：阶段一、阶段二代码工作**全部完成**；改造相关测试 **24/24 全绿**；**工作区改动尚未 commit**；阶段三（定时任务）未做（功能未讨论）。

### 3.1 设计决策（按 R4 审阅意见）：RuntimeContext 三层分离
```
TrustedPrincipal   可信身份与资源范围：username / workspace_id / host_mode /
                   host_workspace_id / is_api_user / role
                   ——只能由适配层认证后构造，客户端不可自报
TaskParams         本次任务参数：message / images / videos / files / model_key /
                   run_mode / thinking_mode / max_iterations / conversation_id /
                   goal_mode / skill_context_messages / message_source
InternalDirectives 内部执行信息：main_task_gate_token / auto_user_message_event /
                   auto_user_message_payload / preceding_user_notices /
                   approval_timeout_seconds（仅透传机制，默认语义不变）
                   ——普通客户端不可提交，仅内部调用方（通知链/工作流/派发器）使用
```
**默认值解析优先级**：本次显式传参 > 对话元数据绑定 > 会话/用户偏好快照 > 系统默认。会话配置恢复仍在 `get_user_resources` + 会话加载链路内完成。

**RuntimeService 最小接口集**：`create_task(principal, params, directives) -> task_id`；`cancel_task(username, task_id)`；`enqueue_runtime_guidance / enqueue_runtime_pending_message / remove_runtime_pending_message / promote_runtime_pending_to_guidance`；`get_task / get_task_events(username, task_id, offset)`（内部查询接口，CLI/定时任务不必走 HTTP）。审批回答复用现有三个 manager，通过明确关联接入，不进 RuntimeService 首版。

**两步走**：步骤①（兼容期）RuntimeContext + RuntimeService 骨架落地，`create_chat_task` 接受显式上下文，6 处调用点迁移，test_request_context 桥保留作兜底；步骤②（拆桥）get_user_resources 增加显式参数变体，`_run_chat_task` 改走显式路径后删 test_request_context 桥，ensure_conversation_loaded 的 session 回写移出任务路径。

### 3.2 关键风险与对策
1. `get_user_resources` 参数化（最高风险）：host_mode/is_api_user 分支选错 → 静默串工作区。对策：显式变体与 web 路径并存，先任务线程单点切换，回归验证后再推广。
2. 门闸 token 移交语义：InternalDirectives 原样承载；通知链"预占→移交→认领→失败回滚"不变。
3. `task_type="notice"` 互斥豁免保留。
4. 异常回退路径（chat_flow_task_main.py:645-675 直接执行 handle_task_with_sender）保留语义，验收覆盖其门闸/事件/取消生命周期。
5. 事件前台干扰：阶段二不加 source 字段（阶段三产品决策），现有行为不变。

### 3.3 实施内容清单（阶段一/二）
**阶段一交付**：
- `docs/runtime_contract.md`：概念对齐（Session/Run/Schedule/Occurrence/Event）、10 项状态责任表、已有保障约束清单、RuntimeService 契约（含 RuntimeContext 三层模型定义）、调用方迁移表、12 项回归用例 T01-T12。

**阶段二交付**：
- 新建 `server/runtime/` 包：`context.py`（RuntimeContext 三层模型，`from_terminal()`、`principal_from_session_snapshot()`、`to_session_data()`）+ `service.py`（RuntimeService + 进程级单例 `runtime_service`）。
- `TaskManager.create_chat_task` 强制显式 session_data（缺失即 ValueError，i18n key `tasks.missing_session_data`）。
- 6 处调用点全部迁移到 `runtime_service.create_task()`：`server/tasks/api.py`、`server/api_v1.py`、`server/workflow_runtime_api.py`×2、`server/chat_flow_task_main.py`×2（完成通知派发 :613、多智能体 idle 派发 :1416）。门闸 token 移交、`task_type="notice"` 互斥豁免语义原样保留。
- `server/context.py`（989 行）拆分为 `server/context/` 子包：identity / broadcast / personalization / usage / upload / conversation / resources / decorators / reaper 共 9 模块 + `__init__.py` 兼容 re-export（外部 import 路径不变）。
- `get_user_resources` 参数化：新增 `RuntimeIdentity` 显式身份快照（定义于 `server/context/identity.py`，runtime 包引用，依赖方向自下而上无循环）。
- **test_request_context 桥已拆除**：`server/tasks/models.py::_run_chat_task` 不再建立 Flask 请求上下文，任务线程全程 RuntimeIdentity 驱动。
- 审批超时透传管道：terminal 属性 `_approval_timeout_seconds`（默认语义 3600s 不变），`_run_chat_task` setattr → `server/chat_flow_tool_loop.py::_approval_timeout_for()` helper → 4 个 `_wait_*` 调用点。
- 附带修复：`config/_load_dotenv` 对禁读 `.env` 的沙箱环境加 try/except（不再 PermissionError 崩溃）。

### 3.4 测试构成（24/24 绿的明细）
- 改造相关 **24/24 全绿**：
  - `test_server_refactor_smoke`（**6**）
  - `test_runtime_service`（**10**，新增）
  - `test_conversation_model_persistence`（**4**，patch 目标随迁）
  - `test_runtime_identity_resources`（**4**，新增，覆盖 get_user_resources 的 web/host/api 身份路由）
- 验收还含：`python3 -m py_compile` 触及文件全过；测试显式上下文（无 HTTP 请求）受理任务 → 拒绝同对话并发 chat 任务 → 取消；不触达真实模型调用。
- **存量失败 4 项**（conversation_workspace_storage / host_workspace_manager / skills_manager / token_usage_extractor）经甄别与本次改动**零相关，未修**。

### 3.5 遗留待办 / 未验证项
1. **真实运行环境验证**（Web 聊天/停止/审批/workflow 激活/多智能体派发）需**用户重启服务后人工完成**——get_user_resources 分支选错会静默串工作区，是最高风险点。
2. **审批条目 task_id 恒 None**（静态疑点，待运行时验证）。
3. **socket 软 stop 不打断审批等待**（REST 硬取消可以），留待阶段三或独立决策。
4. 工作区改动未 commit。

---

## 4. astrion_gateway_gap.md 的早期结论及其被新核查修正情况

> 来源：`cache_research/gateway/astrion_audit/astrion_gateway_gap.md`（2026-09-07 早期盘点，静态代码分析，未做运行复现）。其结论须与 2026-09-07 的 gateway_work_plan / eval_summary 等新核查对照。

### 4.1 早期主要论断（原文）
1. **API 面**：12 个蓝图 REST（tasks_bp 轮询主线）+ 1 个 Socket.IO 辅助通道；消息发送与进度事件已收敛到 POST/GET /api/tasks 轮询模型；socket `send_message` 已废弃。
2. **事件通道**：单一 sender 抽象（写入事件流 + socket 推送合并）；per-task idx、无全局序号；事件流是任务作用域 + 内存 + 1h TTL（deque 20000 上限，cleanup 1h）。
3. **断线追数据三层**：事件流偏移继续 → 对账接口 resumeTask(resetOffset) 全量重放 → 前端 task_id:idx 去重；socket 重连取一次性 token。
4. **状态归属四层**：①进程内存对象（terminal/task_manager/三个 approval manager/GLOBAL_MULTI_AGENT_STATES/usage/socket-token/stop flags）②运行态文件（conversation/*.json、sub_agents.json、personalization.json、settings.json）③Flask session ④前端本地。**无系统级唯一 owner**。
5. **无统一事件序号/事件总线**（idx 是 per-task，socket 事件不带 idx，通道间无法全局对齐）。
6. **审批/提问/计划 = 进程内存 + 绑定 username+conversation_id（无端认领）**，重启即失；无"哪个端在展示/谁决定"记录。
7. **终端缓存 key 已按对话隔离（好），但实例是共享可变对象**；每请求 attach_user_broadcast 重绑回调。
8. **广播粒度是 user 房间，无 conversation 级订阅/投影**；"补丁不是投影"。
9. **socket-token 发放互踩**（issue_socket_token 清空同用户旧 token）。
10. **"停止/活动"仍假设任务绑定连接**（handle_disconnect 的 has_other_connection / REST running 判断）。
11. **REST-only 客户端存在事件盲区**（标题更新无 running task 只走 socket；token_update/todo_updated/edited_files_updated 全局广播不一定进任务事件流）。
12. **认证两套并存**：session+CSRF（Web/CLI/host-login）与 Bearer token（/api/v1）不互通。
13. **socket-token 发放互踩**、**断线即停任务假设**等多客户端缺陷尤甚。

### 4.2 哪些已被 2026-09-07 新核查推翻或修正（对照 work plan / eval_summary / 子报告）
新核查在 `gateway_work_plan.md §0 参考资料的使用边界` 明确要求：早期盘点的"无唯一 owner / 补丁不是投影 / 恢复机制脆弱"等结论**不能直接当作未修复故障**，须结合新核查理解。逐条对照：

| 早期论断 | 新核查状态 |
|---|---|
| "无统一事件序号 / 无事件总线"作为差距 | ✅ 部分成立：新核查确认事件 idx 是 per-task（models.py:762-782）、无全局序号；但 work plan 已把它列入**后续独立决策**（durable 事件），不构成阶段一/二/三的前置条件。子报告确认事件链路"可直接复用（需适配层）"。 |
| "状态真源分四层、无唯一 owner"、"补丁不是投影" | ⚠️ 需谨慎：work plan §0 明确不能直接当未修复故障；阶段一目标正是"固定状态责任表、让权威关系明确"，阶段二哲理性反对"把门面转发当 owner 完成"。这是**改造目标陈述**而非已证故障。 |
| "恢复机制脆弱"（断线靠前端从 0 重放 + 去重） | ⚠️ 修正：work plan 现状确认客户端恢复保护（task.ts 偏移轮询 + probe.ts 对账 + lifecycle.ts 去重）**是要保留的已有保障**；事件链路 sub 报告判定可直接复用。脆弱性被纳入阶段三"持久化选型"与"只恢复计划/记录"的边界。 |
| "socket-token 发放互踩"（issue_socket_token 清空旧 token） | ⚠️ 未复现：work plan §1 保留为**待核验项**（"本轮未重新复现；若仍存在作为独立小范围缺陷处理，不捆绑整个运行时改造"）。早期报告自标"很大概率（依赖时序竞争）"。 |
| "审批状态进程内存、重启丢失、无端认领" | ⚠️ 部分成立并纳入阶段三：子报告确认三个 manager 纯内存、无 TTL/清理；work plan 阶段三列为"重启后旧请求不能被当作有效审批"、审批持久化列为**后续独立决策**。 |
| "停止假设任务绑定连接"（handle_disconnect 判断） | ✅ 方向正确：work plan 阶段三要求"任务生命周期独立于任何连接"；但子报告（R6）补充**标准停止按钮已是 REST 硬取消、可打断审批等待**，软 stop 缺口才需处理。 |
| "REST-only 盲区 / 广播按 user 房间" | ⚠️ 部分成立：work plan 把对话级订阅列入后续独立决策；事件链路判定可直接复用但需处理"前台干扰"。 |
| "认证两套并存不通" | ✅ 成立但列为后续独立决策（work plan 身份体系整理，明确**不能直接合并 Web/API 用户数据空间**）。 |

> 结论说明：早期盘点提供**素材与差距方向**，但多数"差距"已被新核查重新定位为"后续独立决策"或"保留的已有保障"；**没有一条被新核查完全证实为当场需要修复的 bug**，仅 socket-token 互踩与审批 task_id 恒 None 保留为待核验疑点。更详细的早期事实（蓝图清单、事件类型、状态归属表）见 astrion_gateway_gap.md 原文，此处不重复全文。

---

## 5. 外部参考借鉴点（来源：opencode_architecture.md / openclaw_gateway.md）

> 按 work plan / eval 定位：仅提炼被本项目认可的职责分离 / 契约 / 幂等 / 恢复设计，**不复制其部署、存储或认证方案**；外部实现未在本轮重新核验。

### 5.1 opencode 被本项目认可的设计原则（3-5 条）
1. **事件 durable 化 + after=N 重放续传**：SQLite 事件表（aggregate_id, seq, type, data）+ `durable(after)` 先重放历史再续传实时；"live-only delta 不入库、ended 终值入库可重放"降本。→ 对应 work plan 后续"durable 事件与快照恢复"及五条硬边界。
2. **"单进程多 workspace 懒加载实例 + 请求头路由实例"**（`x-opencode-directory`）而非一项目一进程 → 对应 work plan "单活动调度器/单进程"思路。
3. **协议包与实现分离 + handler 注入**（protocol/server/core 分层）→ 对应 RuntimeService 职责单一、避免把 manager 塞进新类。
4. **OpenAPI/协议单一事实源 + 代码生成 SDK**（文档与代码同源，避免过期）→ 对应后续"TS 类型/SDK 生成"决策（单一 schema 权威源）。
5. **审批 = 异步请求对象 + 阻塞等待 + 事件广播 + REST 回复，多客户端共享同一请求 ID、先到先得**（无需连接路由，只需请求对象全局唯一、回复幂等）→ 对应后续"审批持久化与可恢复执行"决策的幂等/认领思路。

### 5.2 openclaw 被本项目认可的设计原则（3-5 条）
1. **"Gateway 单所有者 + 客户端只读投影"**：真状态归 gateway 进程，前端只是 snapshot+增量事件镜像，写操作一律走 RPC 并做冲突检测 → 对应 work plan 状态责任表 / "不能把门面转发当 owner 完成"。
2. **乐观锁（lifecycleRevision + expectedRevision，patch/send 带 expected_revision）**：轻量冲突检测 → 对应持久化"原子更新/唯一性"选型要求。
3. **幂等键（idempotencyKey）**：协议 schema 层强制必填；"内存 Map + TTL + 容量上限 + inflight 共享 Promise + 同 key 同参数校验"以及消息类"幂等键进 transcript + 落盘扫描"持久化去重 → 直接对应阶段三「持久幂等」要求（同触发重试返回同受理结果，同键不同参数拒绝）。
4. **事件不重放 + seq 间隙检测 + 断线重连全量 snapshot**（客户端无状态投影，服务端不缓存事件/只做 per-connection seq 计数）→ 对比 Astrion 现有"从 0 重放 + 去重"前端机制，为将来 durable 事件/重连语义提供参考。
5. **事件广播受众过滤（按 sessionKey + 订阅者集合，只发订阅了该 session 的连接）+ 方法/事件清单白名单下发（feature discovery）** → 对应对话级订阅决策的"订阅与资源授权分别校验"与内部查询接口思路。

> 说明：上述均为"被本项目认可/借鉴"的提炼，不构成对 opencode/openclaw 自身的全面评价。

---

## 6. 遗留问题全集（「待办/待验证/已知缺口」去重汇总，每条注明来源）

> 按来源文档分组；同名跨文档者已去重合并，标注多来源。

### 来自阶段一/二实施记录与项目记忆（phase12 / gateway_runtime_work_plan）
- G1. **真实运行环境验证未完成（最高风险）**：Web 聊天/停止/审批/workflow 激活/多智能体派发需用户重启服务后人工完成；get_user_resources 的 host/Web/API 分支选错会**静默串工作区**。（phase12 §遗留待办 1）
- G2. **审批条目 task_id 恒 None（静态疑点，待运行时验证）**：`getattr(web_terminal,"task_id",None)` 全仓无赋值点；影响审批无法按 task_id 检索关联。（phase12 §遗留待办 2；eval_summary §4；support_chains §2.3 A3；R6 重申）
- G3. **工作区改动未 commit**。（phase12 状态；项目记忆）
- G4. **存量测试失败 4 项**（conversation_workspace_storage / host_workspace_manager / skills_manager / token_usage_extractor），甄别与本次改动零相关、未修。（phase12 §测试验收）
- G5. **socket 软 stop 不打断审批等待**（REST 硬取消可以），留待阶段三或独立决策。（phase12 §遗留待办 3；support_chains §2.4.3；eval_summary §4）
- G6. 阶段二未加事件 source 字段（前台干扰为阶段三产品决策）。（phase12 §风险对策 5）

### 来自 work plan（gateway_work_plan / 项目记忆）
- G7. **两个待核验项**：① `models.py:157`"检查运行中→创建记录"与最终门闸分属两处，并发是否产生重复任务记录未验证；② `server/chat/terminal.py::issue_socket_token` 发放竞争未复现，若存在作为独立小缺陷。（work plan §1）
- G8. **阶段三整体未实施**（Schedule/Occurrence 无代码；幂等/重叠/停机/审批超时/持久化选型全部待实现与讨论）。（work plan §4；项目记忆）
- G9. 后续独立决策均"有明确需求再启动"：SSE/WS/轮询、对话级订阅、durable 事件与快照恢复、审批持久化、TS/SDK 生成、身份体系整理、设备配对/Remote Worker。（work plan §5）

### 来自范围评估与子报告（eval_summary + 四子报告）
- G10. **并发重复任务记录未验证**（同 G7①；工作区/交互层静态推断，不能据此断言并发修改同一对话）。（eval_summary / task_entry_points R3）
- G11. **审批等待循环超时 3600s 且调用点未传参**；超时语义（拒绝工具继续 vs 结束任务）为**产品决策未定**；工具/计划/提问三类超时含义分别定义。R5 明确它不是定时任务技术前提。（support_chains §2.4；eval_summary R5）
- G12. **审批 manager 条目无 TTL/清理**，未决条目永久留在内存；需先定义超时/取消终态与迟到回答处理再设保留期。（support_chains §2.4.4；R6）
- G13. **事件前台干扰**：定时任务事件会推给在线用户 socket 房间，需加 source 或确认产品预期。（support_chains §1.5；eval_summary §3.5）
- G14. **执行链异常回退路径**（chat_flow_task_main.py:645-675）绕过 TaskRecord 登记，迁移验收必须覆盖其记录/事件/取消/门闸生命周期。（R3；task_entry_points）
- G15. **task_id 必须回传并持久化给发起方**（Occurrence 记录 run_id=task_id，取消才可寻址）。（support_chains §3.4）
- G16. 保存链路前提未全验证：新调用方须走对话级 terminal、遵守门闸、会话策略决定 conversation_id；跨实例锁不互斥风险依赖多道防线。（support_chains §4.4）
- G17. `get_user_resources` 约 170+ 调用处（含 83 处 @with_terminal）影响面需 host/Web/API × 会话 × 默认值来源回归验证，不宜承诺每处改动都小。（eval_summary R4；flask_context_deps B-3）

### 来自早期盘点（astrion_gateway_gap.md，多数已被新核查降级/转为决策，见 §4.2）
- G18. 无统一事件序号/事件总线、状态无唯一 owner、审批重启丢失、REST-only 盲区、广播按 user 房间、token 互踩、停止绑定连接、认证两套并存等早期差距——**均已按 §4.2 修正为后续独立决策或保留保障**，勿当作当场故障。（astrion_gateway_gap）

---

## 附：汇总口径与一致性说明
- 本汇总**未掺入子智能体自身推测**，所有小节结论均标注来源文件；文档间冲突（如 R2 计数 15 vs 16、R5 审批超时作为技术前提与否）已在对应处并列呈现并注明修订来源。
- 路径/行号均为各源文档读取时标注，可能因后续代码变动而失效，应以符号/函数名为主。
- 交付物仅 `research_summary.md` 一份，存放于 `cache_research/gateway/audit_research_summary_v2/`。
