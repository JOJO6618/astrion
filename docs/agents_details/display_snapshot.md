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
