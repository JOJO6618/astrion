# 对话级目标模式重构回归

本批次使用临时工作区、合成模型响应及可控 Timer，不访问真实凭证、不请求外部模型、不重启现有服务，不用浏览器自动化代替视觉验收。

## 本批验证范围

- Python 27 项：持久状态、重启转暂停、暂停／继续统计基线、旧 review/save 不覆盖新目标、请求用量防重及对话隔离、可选限制、blocked 手动继续、人工工具拒绝、真实 plan→execute／auto 权限转换及运行中权限排队守门。
- 任务与恢复：五次请求耗尽后的目标级新 user 输入、同一 conversation_id、额度阻塞、停止／关闭／旧任务使 Timer 失效、后台用量改变 revision 不丢重试、暂停通知受理阻断、普通消息不隐式恢复、压缩不接管目标。
- 审核与控制：无工具文本目标可完成、恰好到限制时完成优先、关闭期间迟到审核不续跑、审核网络只重试审核、审核额度阻塞、主 direct 时审核命令仍只读 sandbox、关闭不取消任务、旧 run_id 控制被拒绝。
- Node 10 项：继续与开始参数、已有 unrestricted 保留、关闭不取消任务、迟到开始／关闭响应隔离、revision／工作区过滤、重复点击排重、仅目标也显示 QuickDock、普通和多智能体空页首次目标发送只创建一次对话并使用 execute+auto_approval、草稿保留。

## 命令

```bash
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-10-17_目标模式重构 -p 'test_*.py'
node test/2026-10-17_目标模式重构/frontend.cjs
/opt/homebrew/bin/python3.11 -B test/2026-10-07_运行对话快照/run_regressions.py
node test/2026-10-08_发送受理与对话隔离/regressions.cjs
node test/2026-10-10_侧边栏点击统一与发送守卫/regressions.cjs
node test/2026-10-16_新对话初始化/frontend.cjs
/opt/homebrew/bin/python3.11 -B test/2026-10-16_新对话初始化/backend.py
/opt/homebrew/bin/python3.11 -B test/2026-10-08_主子费用/run_regressions.py
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-10-07_子智能体权限与单次完全访问 -p 'test_*.py'
node --test test/2026-10-07_子智能体权限与单次完全访问/test_approval_state.mjs
npm run lint
set -o pipefail
npm run build --silent 2>&1 | tail -n 5
git diff --check
```

本次 27 项 Python、10 项 Node 目标回归通过；关联显示快照、发送守卫、新对话初始化、主／子费用和权限后端 35 项回归通过。生产构建（TypeScript、stylelint、Vite）通过，本次修改的 TS/Vue 文件 ESLint 为 0 errors / 0 warnings。

整库 lint 为 0 errors / 171 warnings，警告均位于未修改文件，因 max-warnings=0 未通过。旧审批前端回归 `test_approval_state.mjs` 在装载阶段报 `Unexpected dependency ./aiStream`，夹具缺少现有导入，未进入用例；已核对 HEAD 的 `taskPolling/tool.ts` 也存在该导入，本次未修改这份业务文件或旧夹具，不报告该套回归通过。

## 手工验收

1. 在空页及已有普通／多智能体对话分别从快捷菜单打开目标弹窗。取消不发消息，开始后保留同一对话 ID，草稿不丢失，QuickDock 出现目标状态。
2. 从 ask/plan + readonly/approval 开始，核对 execute + auto_approval；已有 unrestricted 或 auto_approval 保留。尝试 ask_user、submit_plan、完全访问申请，核对没有人工等待面板。
3. 执行中关闭目标，核对当前任务继续自然执行；重新开始一项目标后按输入栏停止，核对暂停且可以继续。
4. 在目标活动时重启后端，核对只恢复暂停状态，没有自动请求模型；点击继续才发送新 user 输入。
5. 用可控测试提供商模拟连续连接／超时故障，观察请求内重试耗尽后的等待重试时间及同对话新 user 激活；模拟明确 usage_limit，核对 blocked 且可手动继续。
6. 查看经典／浅色／深色与移动端的弹窗、目标窗口及按钮；切换对话、工作区和语言，核对没有旧状态覆盖。

上述自动化没有调用真实 API，因此不能替代真实服务的网络恢复、提供商错误格式或模型达成质量验收。审核取证只读由作用域与现有沙箱链路保证，本批不新增跨平台内核安全认证。
