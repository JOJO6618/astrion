# 运行中对话显示快照（完整）

> 本文由 `AGENTS.md` §19 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 19) 运行中对话显示快照（2026-10-08）

- **显示加载唯一入口**：`server/conversation_bootstrap.py` + `static/src/app/methods/conversation/bootstrap.ts`；契约见 `docs/运行对话显示快照契约.md`。服务端 `conversation_view/` 冻结已提交历史前缀并增量维护当前任务显示投影，同一显示事务捕获投影、主任务摘要和绝对 `next_event_idx`；前端离线构造后一次提交，再用 `attachSnapshot` 从该游标续接。有限事件 deque 只用于增量，不用于重建全部显示。
- **禁止恢复旁路**：不要恢复 `restoreTaskState`、从事件 0 重放、末条角色/工具猜边界、文本匹配去重、全历史扫描补工具注册、mounted/watcher 第二次恢复、对账独立写消息/归零游标或全局 task handler。新任务从 0 接收自己的新事件属于正常发送路径。
- **所有权**：导航使用 `conversation/session.ts` 的 session 身份与 epoch，空页面也推进 epoch；轮询独立使用 generation/request owner。所有异步响应、catch/finally、timer 在写状态前检查归属，不能只比较 conversation/task 字符串 ID。辅助请求复用 `auxiliaryOwnership.ts`，实时更新及交互处理完成需失效较旧同类 GET。
- **身份与生命周期**：事件和持久化共享用户 `message_id`；动作使用 task/event 身份，工具 preparing/execution/call 别名在 hydrate 一次登记。重试只重置明确动作区间。压缩提交后 rebase 显示前缀并接管新快照；检查点按用户/工作区/对话退役旧显示。前台结束但后台仍活动时保持活动状态；新任务绑定释放旧完整显示副本。
- **存储边界**：`read_conversation`（兼容名 `load_conversation`）不做迁移回写；同持久化目录共享锁保护完整读合并写。锁顺序为显示锁→任务锁，不在任务锁内反向获取显示锁；持久化提交释放目录锁后再发布显示事件。仍维持单后端进程约束。
- **回归**：`test/2026-10-07_运行对话快照/`，统一入口 `python -B test/2026-10-07_运行对话快照/run_regressions.py`（需已安装项目依赖及 Node；本机验证解释器为 `/opt/homebrew/bin/python3.11`）。测试隔离运行态、不启动服务、不读真实对话。视觉由用户验收，首次历史仍全量读取，不将离线回归等同于所有卡顿消失。
- **跨对话索引（2026-10-09）**：`ui/workspace.ts` 的 `/api/tasks` 覆盖所有工作区，使用独立请求身份，不使用对话内辅助请求的导航 session；新请求撤销旧请求写入权，旧 catch/finally 不得清状态或重排定时器。切换对话不停止刷新，失败保留缓存并重试，卸载时取消请求和 timer。空闲输入被任务缓存拦截前先刷新核实；不能把网络失败当空闲。任务索引补同步后台标题，最近完成且仍为占位标题时最多继续检查 90 秒。
- **标题缓存（2026-10-09）**：实时事件、快照与补查共用 `applyConversationTitleUpdate`，由 `conversation` store 原地更新双类型列表、工作区分组、搜索与任务标题，标签栏同步。补查 `/api/conversations/<id>/metadata?workspace_id=...` 不选择后端当前对话、不重载显示；请求和重试 timer 仍服从对话 session，导航 ABA 后不得写入或续排。

## 工作区导航与轻量读取（2026-10-12）

- 工作区和对话是同一个导航身份。`ui/workspaceNavigation.ts` 并行准备选择、目标草稿与 bootstrap，目标准备成功后一次提交工作区、对话、输入作用域、模式与任务订阅。加载期间旧视图保留且旧 session 退役；迟到响应必须检查 session/epoch。选择写入由 `workspaceSelection.ts` 串行完成，不能取消后立即派发下一次选择写入。
- `workspaceRequestScope.ts` 在派发时为同源应用 API 捕获可见工作区，保留显式 header；query 工作区优先于 header，header 优先于 body。服务端验证对话属于该工作区，普通查询不得更改选择身份。任务创建必须从已验证的工作区构造 principal，不能依赖切换中的 Cookie。
- 首次页面进入由 `workspaceStartup.ts` 先用无显式工作区的 `/api/workspace-view` 获取认证后的真实选择，再恢复草稿和绑定路由 session。未知初始作用域不得猜为 `default`；零工作区不读取草稿，身份就绪前的输入保持在本地。这一顺序独立于完整设置/目录初始化，避免路由等待自身尚未开始的初始化。草稿读取失败须反馈并让后续页面初始化继续。工作区目录刷新只更新目录、默认项与运行计数，不能用 Cookie 的选择覆盖已提交视图；失败保留可用目录和可见身份。
- `server/context/workspace.py` 解析可信身份和路径；`data.py` 提供真实存储上下文；`resources.py` 仅装配执行实例。bootstrap、列表、草稿、Token、模式、状态、Git、终端数量等读取不得为查询而创建 WebTerminal、容器或复制技能。只读 ConversationManager 不 mkdir、迁移或修补索引；缺失/损坏索引可在内存重建。执行侧存储准备按数据目录合并。显式多智能体索引修复只写所属存储，不装配执行资源；Docker 项目列表保留旧布局发现，但完成迁移且无旧目录时只做固定路径检查，不为目录查询准备共享配置或运行环境。
- 快照捕获、绝对事件游标与显示锁→任务锁顺序保持不变。bootstrap 复用捕获的数据构造队列、待办、Token 与预览信息；有效模式共用 `modules/conversation_modes.py`，待生效模式随视图一起提交。需要恢复的子智能体由 `context/background.py` 按完整身份异步合并激活，`restore_for_activation()` 保留固定权限、原路径校验及恢复为 idle 的规则。不能用缺少 terminal 推断无后台工作，也不能让恢复阻塞完整历史显示。
- 技能准备的成功版本属于工作区，实现在 `modules/skills_sync.py`；锁内重新核对启用配置、源与目标内容版本，失败不记录成功，项目 `.agents/skills` 原地读取。运行实例并发获取合并；原路径仍有活动工作时拒绝替换实例，失败的未发布实例清理执行资源。
- 统一导航收尾只刷新一次辅助状态，currentConversationId watcher 不再额外 GET 草稿、版本及 Token。工作区列表缓存按工作区/类型/筛选保存，空列表也是有效缓存；刷新与失效都使用请求 owner。全工作区任务索引继续使用页面级归属。纯工作区切换通过轻量 workspace-view 查询最新活动主任务，复用已准备的选择和草稿进入该对话，否则提交空视图。
- 草稿按真实工作区排序保存，来源只保存一次；加载期间新增编辑另作明确补保存。旧 ACK 不清新输入，恢复期间不能把来源编辑器元数据捕获为目标草稿。后端使用目录共享锁、独立临时文件及原子替换，并保存 client/sequence 水位；空草稿保留水位以拒绝迟到 beacon。失败保留本地内容并提示，不把排队 beacon 当作落盘成功。
- 新回归目录 `test/2026-10-12_工作区切换重构/`，具体命令见其 README。沿用运行快照、发送守卫、标题、子权限和费用回归。离线通过不代表所有环境的速度达标；2026-10-12 用户本机实测反馈无明显问题，跨工作区加载速度已接近同工作区切换对话。临时计时接口、请求头、包装器与插桩已移出主线，原采样日志保留为本地证据；未取得新版本量化耗时，不宣称具体提升百分比。

## `/new` 配置准备（2026-10-16）

- GET `/api/workspace-view` 只返回可信工作区身份和活动主任务，不再另行装配空页模式。进入 `/new` 明确调用 POST `/api/new-conversation/settings`，这是允许取得工作区 terminal 的准备操作，仍不得创建对话文件；真实对话仅在首消息按完整前端快照创建。
- 显式新建即使目标工作区有活动任务也保持 `/new`；普通工作区切换保留优先进入活动对话的行为，无活动对话时再准备空页。导航来源只在同工作区显式新建时传刚离开的可见对话 ID，跨区传 null。
- 配置、工作区、空页与草稿沿原导航 session 一次提交；保留空页 session，避免提交后退役使 loading/finally 无法清理。请求返回必须检查归属；失败恢复原可见身份。空页本地选择不回写共享 terminal，状态轮询不得覆盖；首消息捕获一次快照，创建与首任务使用同一份模型/思考配置。
