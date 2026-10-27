# 不切换 Agent 后的实时续聊显示

用户现象：点击“不切换”后，只显示服务端新追加的 user 消息，AI 头部和正文直到整页刷新才出现。

根因：HandoffCard 的 `changed` 事件调用 `useChat.reload()`，原实现只应用一次快照，没有像 `load()`／`send()` 一样为快照中的新任务启动轮询。若 worker 尚未输出内容，该快照就只有 user；后续头部和正文无法送达当前页面。

修复：`reload()` 获取并应用统一快照后，从该快照的 `next_event_idx` 接管任务轮询；空闲／已完成快照不持续轮询。`schedule()` 在清理定时器前检查导航所有权，旧页面的迟到加载不能清掉新页面的任务轮询。沿用既有显示投影，不添加第二套恢复链，不从事件 0 重放。

```bash
node 'test/2026-10-28_Chat交接续聊显示/frontend.cjs'
node 'test/2026-10-25_Chat模式/frontend.cjs'
node 'test/2026-10-27_Chat交接标签/frontend.cjs'
./node_modules/.bin/eslint static/src/chat/useChat.ts --max-warnings=0
npm run build --silent 2>&1 | tail -n 5
```

本目录 5 组回归执行生产 useChat、真实 RuntimeProjection 生成的投影和共享 ChatArea 模板，覆盖：

- 不切换受理后初始只有 user，轮询自动收到 AI 等待头部、实时正文和最终状态，无需第二次加载。
- 使用快照绝对游标（17／29），同一时间只保留一个轮询定时器，任务结束停止轮询。
- 已经完成的续聊直接展示快照，不额外启动轮询。
- 旧页面迟到的 load／reload 不覆盖新页面、不取消新任务轮询。

原有 11 组 Chat 状态及 6 组交接卡片检查通过。共享模板测试夹具补齐生产代码已使用的 mergeAssistantDisplayRuns 依赖；不修改共享 UI 产品逻辑。

这些是离线可控请求与模板检查，不调用真实模型、不启动／重启现有服务；真实浏览器／桌面端效果仍由用户验收。
