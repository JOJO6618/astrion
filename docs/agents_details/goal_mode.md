# 对话级目标模式（2026-10-17）

## 产品行为

- 入口是输入栏快捷菜单中的目标模式。点击打开目标弹窗，填写目标，点击开始才发送真实 user 输入；草稿输入框中的内容不被当作目标，也不被清空。
- 空页首次开始沿用正常新对话创建流程，只创建一次。后续审核、压缩、重试、暂停和继续全部保持同一个 conversation_id。
- 启动／继续会将 work_mode 设为 execute；权限已为 auto_approval 或 unrestricted 则保留，其余设为 auto_approval。不临时恢复 plan 之前的 direct 权限。
- QuickDock 的目标窗口独立显示目标、状态、最近信息及控制。仅有目标时也能显示 Dock。状态详情弹窗可调整本次继续区间的可选限制。
- 关闭目标只结束目标管理、审核及重试，不调用任务 cancel，不停止当前任务。关闭后保持已生效的工作／权限模式。
- 输入栏停止会取消当前主任务并持久暂停目标。paused 和 blocked 均可手动继续；不要求先探测阻塞已解除。普通用户消息不会把 paused/blocked 隐式改为 running。
- 进程重启不自动续跑目标；首次读取发现旧进程的活动状态时，原子校准为 paused，原因 process_restarted，清掉重试时间。前端从 bootstrap 恢复文件快照并提供继续按钮。

## 唯一权威与身份

- 唯一权威：`<工作区 data_dir>/goal_states/<conversation_id>.json`，由 `modules/goal_state_manager.py` 读写；TaskRecord.goal_progress 和前端 store 都是显示投影。
- 状态：running / reviewing / retrying / paused / blocked / done / stopped（界面显示“已关闭”）。前三个是活动状态；paused/blocked 可继续。
- run_id 标识一项目标，继续保留；新目标换 run_id。task_id 标识当前接管目标的主任务，process_id 标识当前进程。revision 对所有文件更新单调递增，前端拒绝旧 revision 事件。
- 所有同路径 manager 共用 RLock；文件写入采用随机临时文件、fsync 和原子替换；旧实例 save 不得覆盖较新的 revision。
- 自动重试回调绑定 run_id + task_id + next_retry_at，并检查当前 Timer 身份。不能绑定总 revision：后台子任务的用量写入也会递增 revision，不应使唯一的重试丢失。
- 锁顺序：显示事务 VIEW_LOCK → 目标状态锁 → 必要的任务查询锁。持目标状态锁时发布 sender 事件，必须已经拥有 VIEW_LOCK；费用账本锁释放后才进入目标状态锁。
- 手动压缩任务不接管目标的 task_id；自动压缩仍在原任务中续接，重注入目标规则，不能重新 start 或更换目标／对话 ID。

## 受理、控制与显示

- `/api/tasks` 使用 goal_action=start|continue 与可选 goal_options={max_turns,max_tokens}。旧的 goal_mode=true 布尔启动入口被拒绝，不能恢复“下一条输入成为目标”的 armed 语义。
- 开始／继续走 RuntimeService.create_task → TaskManager，维持同一对话主任务互斥、门闸及显示快照链路。状态在受理、起线程前保存；执行线程加载目标对话后校准工作／权限模式。
- `POST /api/conversations/<id>/goal` 仅接收 close|pause 和当前 run_id；旧目标控制请求返回冲突。控制不创建执行实例、不取消主任务，通过已有任务事件流发布快照。
- bootstrap 与 running-status 都读取文件状态。前端 bootstrap 是唯一恢复入口，不新增 mounted 恢复旁路；控制响应和目标事件均受会话 session、工作区和 revision 检查。
- 暂停／阻塞时，传统后台通知和多智能体通知在消费前等待，受理层再拒绝自动通知；传统目标通知禁止降级为不带 TaskRecord 的直接执行。多智能体通知的池消费及同步任务受理处于同一 VIEW_LOCK 事务。

## 无人工交互

- 活动目标的执行层拒绝 ask_user、submit_plan 和 request_full_access=true；Web ask_user 批处理在创建问题前跳过，常规工具结果仍保持 tool_call 配对。
- 直接 handle_tool_call 入口再次拒绝上述工具与 NEED_CONFIRMATION 中需要询问的工具。目标活动期间不能切到 ask/plan 或人工审批／只读权限。
- 正常自动审核允许；审核拒绝交还模型调整方案，不要求人工批准。智能体间 ask_master/ask_other_agent 是内部通信，不按人工交互禁用。
- 目标规则同时禁止等待人工登录／回答／批准。MCP 客户端目前不支持 elicitation/create；任意外部工具内部自行打开登录网页或等待输入无法靠工具名完整识别，不能宣称已对所有第三方工具行为做静态判定。

## 故障、调度和审核

- 主模型当前请求内保留最多五次尝试的机制。最终失败事件标 fatal，使 TaskRecord 以 failed 结束；普通工具错误不据此把整个主任务判失败。
- 网络、连接、超时、临时限流和 HTTP 5xx：主任务退出且门闸释放后保存 retrying，后端 Timer 等待 30 / 60 / 120 / 240 / 300 秒（封顶）重新发起 goal_recovery user 消息。消息提醒核实已执行操作，避免重复副作用。
- 明确额度耗尽、认证失败、配置错误和未知错误进入 blocked，不无限自动重试。HTTP 429 仅在不含明确 quota/usage-limit 证据时视为临时限流。继续会再次执行，若条件未恢复可再次阻塞。
- 关闭／暂停取消 Timer；旧任务结束不得修改新 task_id 对应的状态。应用启动不扫描并重新安排旧 Timer。
- 审核模型失败时单独重试审核，不让主模型重复执行已完成操作。审核响应保留提供商状态码、错误类型／错误码以分类。
- 审核输入包含原目标、近期审核与有限的实际 user/assistant/tool 证据，排除思考内容；结论是 done / continue / blocked。文本任务无工具也可以完成，不要求无关测试或把明确交给用户的视觉验收变成无限自动补做。
- active 取证模式命令绑定独立 sandbox scope 且 sandbox_write_access=False；主对话处于 direct 也不能让审核命令获得写权限。readonly 审核模式只读传入对话证据。
- 先审核是否达成，再检查可选轮数／Token 限制；在恰好达到限制时完成的目标可以 done。重复的主输出＋审核意见连续出现会因 no_progress 阻塞。

## 限制与统计

- 默认不设轮数或 Token 上限；目标弹窗显式启用，两个限制均可独立关闭。设置页只保留审核方式及审核智能体配置，旧全局目标限制不再作为运行参数入口。
- 继续保留累计统计，但从新的 turn_baseline / token_limit_baseline 开始计算本次授权执行区间的限制。
- Token 统计来自同一对话新提交的主／子请求费用账本 total_tokens，按 request UUID 防重，不使用工作区总量差分。三个审核智能体本来不计入费用账本，因此也不计入这个目标 Token 上限。
- 限制在执行／审核边界检查，是软上限，单次模型请求和后台子任务可能使累计值超过阈值；不把它当作硬预算拦截。

## 验证

见 `test/2026-10-17_目标模式重构/README.md`。自动化回归不等于真实模型／真实网络恢复已验收；界面视觉由用户检查，不用浏览器自动化替代。
