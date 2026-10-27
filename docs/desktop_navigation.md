# 桌面标签导航契约

2026-10-23：适用于 Electron macOS 与 Tauri Windows。顶部独立 WebView 保留，导航状态移到后端。修改标签身份、关闭、恢复、绑定、页面往返前必读本文。

## 唯一写者与状态

`modules/desktop_navigation/` 是唯一标签写者。`server/conversation_tabs.py` 装配认证用户、可信工作区、存在性查询及运行态；不得为标签查询创建 WebTerminal、容器或读取完整历史。

存储仍为 `<DATA_DIR>/conversation_tabs.json`，按用户名分键。version=2，单后端进程内 RLock 串行处理命令，以原子文件替换提交。每个成功命令递增 revision；GET 仅在迁移或核验导致持久状态变化时递增。

- `tabs[]`：最多 100 个。`key=tab_*` 贯穿 new→conv 绑定及关闭，不能用对话 ID 或双页计数器作 key。
- 对话唯一身份包含 `conversation_kind`（默认 `agent`）及 `(workspace_id, normalized conversation_id)`；相同 ID 的不同工作区分别显示与计算运行态。Chat 使用 `conversation_kind=chat` 和空工作区，不解析成 Agent 默认工作区，标签外观不添加模式标记。
- `active_key`：已经确认提交的内容标签。`resume_key`：独立页面返回目标。
- `navigation`：最新请求，含 `id/key/page/status/error`，status 为 pending、committed 或 error。加载期间可同时保留旧 active 和新目标；禁止把请求已受理当成内容已提交。
- `renderer_id`：主页面当前文档身份。刷新后重新 attach，旧文档失去提交权。
- `receipts`：最近 256 条命令的身份、参数指纹、结果，仅内部保存。
- `closed_keys`：最近 2048 个关闭身份，仅内部保存；客户端每次新建都使用随机 UUID，不重复使用 key。

旧 `{tabs, active_key}` 记录一次迁移，保留顺序、按工作区＋对话去重并映射 active。损坏 JSON、无效结构及未知版本明确报错，不当成空状态覆盖。

## HTTP 命令

`GET /api/conversation-tabs` 返回带 revision 的权威快照。`POST /api/conversation-tabs/commands` 接收：

```json
{
  "command_id": "unique-request-identity",
  "action": "activate",
  "payload": {"key": "tab_uuid"}
}
```

响应含 `state` 与 `result`。同一 command_id、相同参数在 receipt 保留窗口内只执行一次，重试返回当前快照及原结果；同 ID 异参返回 409。客户端网络结果不确定时只重试同一个 envelope，不能重新生成 ID。业务失败不自动当成网络失败重试。

| 动作 | 行为 |
| --- | --- |
| create/open/workspace | 新建空标签、打开历史或进入工作区的活动主任务/空页，生成导航代次 |
| activate | 选择现存稳定 key，生成新代次，允许重新尝试加载错误目标 |
| close | 立即移除目标并选择继任者；最后一个实际对话关闭后同事务补空标签 |
| bind | 将发送前捕获的空标签绑定真实对话，不改变 key；源标签已关返回 bound=false，不复活 |
| page/resume | 设置、工作流、Git 等独立页往返，共用导航身份 |
| attach | 注册新 renderer；刷新继承最新 pending 目标，旧地址不能覆盖尚未完成的切换 |
| commit/fail | 必须匹配 renderer_id＋navigation_id；error 状态不能再 commit，需重新 activate |
| title | 仅更新明确工作区＋对话的标题 |
| deleted | 仅服务端删除成功钩子可调用，客户端不得伪造 |

旧全列表 PUT 及 `/api/desktop/chrome-dispatch`、原生 `/chrome/dispatch`、`__astrionChromeDispatch` 队列已经移除。禁止恢复 eval 标签意图旁路、双页本地结构修改和乐观 TTL 覆盖。

## 主页面内容提交

`static/src/app/methods/ui/desktopNavigation.ts` 是唯一桌面导航协调器，`conversationTabs.ts` 只投影权威状态，`desktopNavigationTransport.ts` 串行发送短命令。命令串行不等待完整历史加载，关闭和新目标不会被旧加载阻塞。

**启动顺序（2026-10-24 白屏修复）**：`bootstrapRoute` 完成后先执行 `loadInitialData`，待其成功或失败收尾后再恢复桌面导航。禁止在初始化尚未完成时触发标签恢复：恢复会推进 conversation epoch，使初始化的所有权检查直接退出，跳过个性化设置与工作区目录；`ChatArea` 等待个性化设置就绪，因而持续隐藏历史内容。卸载时撤销页面存活标记，迟到的路由或初始化不能再次 attach。回归：`node test/2026-10-24_桌面启动白屏/startup.cjs`。

1. 主页面 hydrate 后 attach 文档，注册最新目标观察。
2. 目标复用 `workspaceNavigation.ts` 与 `conversation/bootstrap.ts`，并行准备工作区选择、草稿和显示快照。
3. 内容准备好后请求 commit；后端拒绝被关闭、删除、替换或旧 renderer 的提交。
4. 回填快照后再次检查本地 renderer/navigation 和 session/epoch，才提交显示及任务订阅。
5. 失败标记目标错误，原 active 视图经原入口受控恢复，不新建标签；原 active 是空页时按其原工作区恢复空页。

**观察器必须在整次状态更新后消费。** Pinia 对嵌套 `$patch` 逐字段写入，`flush:'sync'` 曾使观察器拿到 B 的 navigation.id 与 A 的 key，把 B 提前标记 handled。当前使用 `flush:'post'`；不能因希望降低延迟改回同步监听。

保持原唯一显示快照、任务事件游标和串行工作区选择机制。桌面 URL 在会话/空页路径保留 workspace_id，刷新不猜默认工作区。发送前校验标签、可见 session、工作区及对话一致；请求目标尚未提交时不能发送到旧内容。新建首消息使用发送前捕获的源 key 绑定，即使其间切到另一个标签也不改绑当前 active。

主页面 attach 失败自动重试，单次生命周期只提示一次启动错误。卸载撤销观察、重连 timer、轮询 owner 和请求，迟到响应不得重新开始轮询。

2026-10-26 Chat：URL 为 `/chat/new`、`/chat/<id>`，不携带假工作区；继续使用同一个 `startDesktopNavigation` 协调器。首消息捕获真正 `new/chat` 标签的 key 进行绑定，迟到受理不能改绑当前标签。Chat 空页刷新复用原标签，纯 Chat 设置页返回恢复原模式；查询坏 Chat 文档仅标记对应标签错误。跨模式导航整页切换，Chat 页面不会挂载 Agent 的工作区初始化。

## 关闭、删除与恢复

关闭命令在点击时即提交，230ms 关闭动画只投影展示，不延迟操作。关闭标签不停止后台任务，也不删除真实对话。

真实删除成功后服务端移除匹配工作区＋对话的标签、撤销 pending 提交权。标签文件同步失败不能把已经成功的对话删除报告为失败；返回独立同步错误，前端保持发送守卫，由后续核验清理。

2026-10-27 对话工作区迁移：仅服务端调用 `DesktopNavigation.relocated`，原位更新匹配 Agent 标签的工作区和标签文字，保留 key、顺序及标签数；目标是当前导航时重新生成 navigation.id，独立设置页不被强制跳走。目标完整落盘及迁移回执持久化后，在导航锁内重定位并退役源文件，避免 GET 核验误把迁移当删除而补空标签；同 operation 重试可补完收尾。禁止通过 deleted＋open、客户端标签列表写回或新增公开 relocate 命令实现。详见 `docs/conversation_transfer.md`。

GET 启动时及最多每 5 秒轻量核验：确认对话文件不存在才移除；工作区不可访问或读取异常保留错误标签，禁止把权限/网络错误误判成已删除。运行态与标题富化沿用轻量查询，不借用其他工作区的实例。

轮询为一次完成后再排下一次（间隔 500ms），不允许 setInterval 重叠请求；客户端同时比较服务端 revision 与本地响应序号，拒绝旧状态。请求超时 8 秒，网络不确定的命令重试一次相同 envelope。文件损坏或服务不可用时保留错误反馈，不上传空列表覆盖。

## 验证与发布边界

回归入口：

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-23_桌面导航重构/run_regressions.py
```

覆盖持久化命令、Flask handler 装配、真实 Vue/Pinia 与真实 bootstrap 导航竞态。API 测试使用合成身份替代认证装饰器，不等于认证或原生壳端到端验收。

配套跑工作区、显示快照、草稿/运行引导及标签运行态回归，前端定向 ESLint 和生产 build。不得把离线验证等同于安装包已更新或 macOS/Windows 真机已验收；视觉交互由用户完成。此协议前后端需一起更新，旧前端的 PUT/桥派发不兼容新后端；发布时随同前端产物及 Python 模块打包。
