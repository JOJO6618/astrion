# 桌面标签导航契约（v3 多视图）

适用于 Electron 桌面壳（macOS + Windows）。主窗口的 chrome 视图常驻顶部标签栏和左侧栏，每个标签有独立的内容 `WebContentsView`。导航状态在后端。`docs/desktop_multi_view.md` 记录多视图设计沿革。网页端和 Android 继续使用单页布局。

## 唯一写者与状态

`modules/desktop_navigation/` 是唯一标签写者（`model.py` 结构与迁移，`commands.py` 命令，`service.py` 存储/幂等/核验）。`server/conversation_tabs.py` 装配认证用户、可信工作区、存在性查询及运行态；不得为标签查询创建 WebTerminal、容器或读取完整历史。

存储 `<DATA_DIR>/conversation_tabs.json` 按用户名分键，version=3，单后端进程 RLock 串行，原子文件替换。每个成功命令递增 revision；GET 仅在迁移或核验导致状态变化时递增。

- `tabs[]`（最多 100）：`key`（`tab_*`，贯穿 new→conv 绑定与关闭）、`kind`（`conv|new|page`）、`conversation_kind`（`agent|chat`，页面标签无）、`conversation_id`、`workspace_id`、`workspace_label`、`title`、`error`、`placement`（`main|detached`）；页面标签另有 `page_type`（`settings|workflows|git`）、`route`、`opener_key`。数组顺序即主窗口标签顺序，detached 标签留在数组中但不在标签栏。
- 去重：conv 按 `(conversation_kind, workspace_id, normalized conversation_id)`；page 按 `page_type`（git 再加 `workspace_id`）。Chat 用空工作区。
- `active_key`：主窗口当前标签（唯一权威，必须是 main 标签或空）。`workspace_id`：最近激活的 Agent 标签工作区。
- `renderers {key: renderer_id}`：每个内容视图当前文档身份。`receipts`（256）与 `closed_keys`（2048）仅内部保存，不对外。
- 已删除：`navigation`、`resume_key`、单一 `renderer_id`、`commit`、`resume`。每个视图一生只绑定一个标签，不再有「同一文档改指向另一目标」。

迁移：v1/v2 一次迁到 v3（保留顺序、全部 `placement=main`；active 取 `active_key`→`resume_key`→首个标签；丢弃 navigation 与 renderer_id）。损坏 JSON、无效结构（含悬空 active、detached 的非对话标签、缺 workspace 的 git 页）与未知版本明确报错，不当空状态覆盖。

## HTTP 命令

`GET /api/conversation-tabs` 返回带 revision 的快照；`POST /api/conversation-tabs/commands` 接收 `{command_id, action, payload}`，响应 `{state, result}`。同 command_id＋同参数只执行一次，同 ID 异参 409；网络不确定只原样重试同一 envelope。

| 动作 | payload → 行为 |
| --- | --- |
| create | `{conversation_kind?, workspace_id?, key?}` 新空白标签并激活 |
| open | `{conversation_kind?, workspace_id, conversation_id, key?}` 已存在：main 则激活；detached 不改 active，返回 `{key, focus:"detached"}`；不存在则新建并激活 |
| workspace | `{workspace_id}` 该工作区活动主任务或空页，之后同 open |
| page | `{page: route}` 按 page_type（git 另按 route 查询中的 `workspace_id`，必填）找或建页面标签，更新 route，`opener_key`=当前 active（非自身），激活。路由仅 `/settings*`、`/workflows`、`/workflow*`、`/git` |
| activate | `{key}` 仅 main 标签；detached 返回 409 |
| close | `{key}` 移除并记入 closed_keys；active 的继任：页面标签先回仍在 main 的 opener，否则右侧相邻、无则左侧；主窗口无标签补空白新对话 |
| reorder | `{key, before_key}` 只改 main 标签相对顺序（`""`=末尾），不改 active |
| detach | `{key}` 仅 `conv`/`new`（页面标签不可）；`placement=detached`，不进 closed_keys；是 active 按右优先选继任；主窗口为空补空白标签 |
| dock | `{key}` `placement=main`，移到数组末尾，active 不变；仍空白的 detached 新对话标签关闭时直接移除标签，不放回主窗口 |
| attach | `{key, renderer_id}` 登记 `renderers[key]`（覆盖旧文档），清标签 error；标签不存在 409 |
| bind | `{key, workspace_id, renderer_id, conversation_id, title?}` 要求 `renderers[key]==renderer_id`；源标签已关返回 `bound=false` |
| fail | `{key, renderer_id, error}` 标记本标签 error；对话已删除则移除标签 |
| title / deleted / relocated | title 客户端可发；deleted 仅服务端删除钩子，客户端发送 400；relocated 仅服务端，对 detached 标签同样生效 |
| focus | `{key}` 只写内存「最近聚焦标签」，不落盘、不增 revision、不写 receipt |

错误码：409 `navigation_superseded`（标签已关、detached 不可 activate/reorder、renderer 不匹配、attach 缺失标签、已删除对话）；400（非法页面、detach 非对话标签、git 无工作区）；400 `no_workspace`（零工作区）。GET 仅存储损坏时 503。

CrossPet 聚焦：create/open/workspace/activate/page/focus/detach/dock/close 之后按「最近聚焦标签（仍存在）否则 active」上报。浏览器回收前台集合 = 主窗口 active 对话 + 所有 detached 对话（`server/browser_retention.py foreground_scopes`）。

## 壳层与视图角色（`desktop-electron/src/views/`）

- ViewManager 持有每标签一个视图；只接受 revision 更大的快照并广播给 chrome 与所有视图（不回显给转发者）。最多 6 个 conv/new 视图常驻（LRU），page 与独立窗口视图不淘汰；淘汰视图再次激活冷加载。
- **chrome 视图**：唯一轮询 `GET`（500ms）并转发每个更新快照；点击标签 `astrionShell.show(key)` 后发 `activate`；拖拽排序发 `reorder`；拖出对话标签或空白新对话标签在指针离开标签条 >40px 后发 `dragTab`（tear/move/drop），壳层建独立窗口并回调 `onRequestDetach`→`detach`；独立窗口关闭 `onRequestDock`→`dock`；窗口聚焦 `onWindowFocus`→`focus`；`onRequestPage`→`page`（快捷入口设置深链）。页面标签不可拖出，Esc 取消。
- **内容视图**：`astrionShell.identity()` 取得固定 `key/placement/visible/state`，`attach {key, renderer_id}`，此后不再切换标签；不轮询标签快照，订阅 `onTabsState`；每个命令响应都 `forwardTabsState`。legacy 兜底页（key 为空）整体降级为网页行为。
- **常驻侧栏**：`DesktopSidebar.vue` 挂在 chrome 视图，复用 `ConversationSidebar.vue`，独立加载工作区目录、搜索、分页和任务摘要。设置、工作流、定时任务、项目 Git 在同一右侧区域打开。侧栏读取当前标签快照，独立页面以 opener 推导列表模式，按实际活动标签显示高亮。内容视图的侧栏初载及轮询由外壳接管。
- **原生布局与弹层**：外壳提供侧栏宽度，右侧内容视图从该宽度起排。侧栏浮层打开时，外壳临时抬到内容和原生网页层上方，关闭后恢复。浏览器按钮按当前标签转发，面板和 AI 操作沿用对话作用域。独立对话窗口使用本窗口的完整内容区域。
- 前端实现见 `stores/conversationTabs.ts`、`app/methods/ui/desktopView.ts` 与 `components/chrome/`。

**所有权与发送守卫**：`isDesktopNavigationReady` = 已 attach ∧ `renderers[key]` 是本文档 ∧ 标签仍在 ∧ 标签身份等于可见 session（new↔无对话，conv↔同对话＋工作区）；页面标签不可发送。首条消息用本视图 key `bind`。侧边栏点击本视图已显示的对话为空操作，其余发 `open`/`workspace`/`create`；`focus:"detached"` 时调用 `astrionShell.focusTab(key)`。加载失败只 `fail` 本标签，不另开标签。

**工作区身份**：视图按 URL 中显式 `workspace_id` 与请求头自带身份启动，桌面下 `selectWorkspaceIdentity` 为空操作，不读写 session 选中工作区（多视图会互相覆盖）；隐藏视图不调用选择接口。

**页面标签**：设置/工作流/Git 页面作为标签常驻（Git 每工作区一个）；页内「返回」= `activate` opener（不在 main 则右侧/左侧相邻），不关闭页面标签；设置分区切换就地进行，`route` 仅在 `page` 命令时更新。

**独立对话窗口（placement=detached）**：`DetachedTopBar.vue` 随 `onPlacement`/快照就地切换（`html.desktop-detached`）：隐藏侧边栏，顶部 38px 栏（标题，`-webkit-app-region: drag`，macOS 左让红绿灯、Windows 右让窗口控制），无新建/切换对话入口；设置/Git/工作流按钮发 `page` 后 `focusMain()`。放回主窗口恢复正常布局，不重载。

**隐藏视图降频**（`ui/viewVisibility.ts`，订阅 `onVisibility`，网页端恒可见）：隐藏时暂停 `/api/status` 空闲刷新、运行态对账、Git 摘要与终端数刷新。任务事件轮询降到约 1 秒，重新可见立即补查并恢复到 250 毫秒。跨对话任务索引由常驻侧栏轮询。

## 关闭、删除与恢复

关闭命令在点击时即提交，230ms 动画只投影展示；关闭不停止任务。真实删除成功后服务端移除匹配标签（含 detached，壳层随快照关闭独立窗口）；标签同步失败不能把已成功的删除报告为失败。对话工作区迁移仅服务端 `relocated`，保留 key、顺序、placement。GET 启动时及最多每 5 秒轻量核验：确认对话文件不存在才移除；工作区不可访问保留 `workspace_unavailable`；页面标签跳过核验（Git 核验工作区）。

## 零工作区（2026-11-13，仍适用）

新装 host 无工作区是正常空态：后端 `WorkspaceRequired` → `400 code=no_workspace`，前端 `command` 对该 code 静默返回（清 `commandError`，**不再跳转**）；`_lookup` 捕获 `NoWorkspaceError`，`_reconcile` 把标签标 `workspace_unavailable` 并保留，GET 不得 503。判断零工作区只用导航快照，不用组件级 `currentHostWorkspaceId`（设置页等独立路由为空）。host 首次启动由 `ensure_default_host_workspace` 建默认工作区。

## 启动期会话预热、端口复用与首帧主题（2026-11-15，仍适用）

**症状**：冷启动时顶部标签条先显示「同步失败，重试」且没有任何标签，几秒后自行恢复；同期顶部条是经典配色、对话区已是用户主题。

**根因（两条独立链）**：

1. 窗口在会话建立前就创建：`chrome` 视图 `onMounted` 立即请求 `/api/conversation-tabs`，而会话要等内容视图加载 `/login`、`LoginApp` 自动 `POST /host-login` 之后才有；这段窗口里 `api_login_required` 返回 401（`server/auth_helpers.py`），前端把任意非 2xx 统一显示成「同步失败，重试」。
2. 后端端口每次启动随机（`pickFreePort` 的 `listen(0)`），而 `SESSION_COOKIE_NAME` 是 `agents_session_<port>`、`localStorage` 也按 origin 隔离：换端口等于全新空存储，首帧读不到主题回退 classic；内容视图随后从服务端 personalization 拿回主题写回 `localStorage`，chrome 条只能靠 `ChromeApp` 的 2s 定时器追上。

**修复（壳层 + 前端兜底）**：

- `desktop-electron/src/session-priming.js`：`waitBackendReady` 之后、任何视图创建之前，用 `session.defaultSession.fetch` 完成一次免密 `host-login`（先 `GET /api/csrf-token`，带 `X-CSRF-Token` 提交；已登录则跳过，避免重复 `session.clear()` 重置会话）。失败只告警，前端登录流程仍兜底。
- `backend.js`：`loadPreferredPort` / `savePreferredPort` / `isPortAvailable` / `resolveBackendPort`，端口记在 `userData/backend-port.json`；`lifecycle.js` 在「探测通过但 spawn 后被抢占」时换随机端口重试一次。
- `window.js`：`shell:theme`（受管视图上报主题键，存 `userData/theme.json`）与 `shell:theme-get`（同步读取，供 preload 在页面脚本执行前注入 `window.__ASTRION_INITIAL_THEME__`）；`preload-chrome.cjs` / `preload-view.cjs` 注入该值，`index.html` / `chrome.html` / `login.html` 的内联首屏脚本优先用它、其次 `localStorage`。
- 前端兜底：`conversationTabs.shouldSilenceSyncFailure` —— 未 `hydrated` 且启动 20 秒内的 401/网络错误不显示成同步失败（`refreshSnapshot` 不抛、轮询只清提示、`command` 只清 `commandError` 但保持重试），超期照常提示。

**验证**：后端 test_client 实测：未登录 `GET /api/conversation-tabs` → 401「登录状态已过期」；`GET /api/csrf-token` → 200；带 token `POST /host-login` → 200 `success`；再取快照 → 200。壳层 GUI（端口复用、首帧配色）需实机启动验收。

## 主题跨视图同步（2026-11-17）

**症状**：设置页切换主题后，从标签条切到别的对话，那个视图仍是旧配色，必须重新加载页面才更新。

**根因**：每个标签是独立 `WebContentsView`，隐藏视图只隐藏、不卸载（`views/manager.js`）。主题是纯文档级操作（`document.documentElement[data-theme]`）；`shell:theme` 原先只把主题键存进 `userData/theme.json` 供下次启动首帧使用，**没有任何广播**，前端也没有跨视图监听。

**实现（壳层广播链）**：

- `window.js`：`shell:theme` 校验主题键后，值真的变化时调用 `views.broadcastTheme(key, event.sender)`。
- `views/manager.js::broadcastTheme`：向 chrome 视图与所有内容视图 `send('shell:theme-changed', theme)`，跳过上报者。
- `preload-view.cjs` 暴露 `astrionWindow.onThemeChange`，`preload-chrome.cjs` 暴露 `astrionShell.onThemeChange`，均返回取消函数。
- 前端 `utils/theme.ts`：`installTheme()` 只订阅一次；收到广播走 `applyTheme(theme, fromShell=true)`——只改 DOM，不再回报壳层，避免多视图来回触发 IPC。`themeChangeSource()` 兼容内容视图与 chrome 两种桥。
- **统一入口**：`utils/theme.ts::applyThemeKey` 是唯一的 DOM 应用入口；`stores/personalization.ts::applyTheme` 与 `app/methods/taskPolling/tool.ts`（智能体经 `manage_personalization` 改主题）都改走它，否则只改当前视图。

**边界**：`quick` 快捷对话窗口与内置浏览器窗口各有 preload，未接 `onThemeChange`（打开时从 localStorage 取主题，不做运行期同步）；内容视图内个人偏好 store 的 `form.theme` 不随广播更新。

**验证**：`tsc --noEmit`、ESLint、`node --check`（壳层 4 个文件）、`npm run build` 通过；2026-11-17 用 `--dir` 测试包（同一数据根 `~/.astrion/astrion-desktop`）实测：设置页切换主题后切到其余标签，配色即时跟随，无需重新加载。

## 人工等待黄点与桌面对话通知

- 快照投影 `awaiting_human_approval` 和 `awaiting_user_answer`，黄点优先于运行点。工具审批只计算仍需人工决定的请求。计划审批和问答通过活动任务 ID 绑定原工作区和对话，纯自动审核不触发。请求处理后下一次快照更新黄点。
- Electron 主进程轮询 `GET /api/conversation-tabs/activity`。该接口通过任务注册表发现已关标签和其他工作区的对话，用与标签相同的主任务、门闸和后台活动判定。两次确认空闲后才通知成功完成，冷启动不补发历史完成通知。待审批和待回答按请求或问题批次去重。
- 前台是其他应用时显示横幅。Astrion 主窗口在前台时只显示应用内状态。前台是独立对话窗口时，只抑制该对话自身的横幅。横幅展示时不抢焦点，点正文跳转并收起横幅，点「忽略」只收起横幅。两种操作都沿屏幕右缘退出。macOS 提交一次 Dock 提醒，Windows 任务栏闪烁最多约 1.8 秒，切回应用时提前收起图标提醒。
- 正文点击由 chrome 视图提交现有 `open` 命令。壳层按当下实际窗口前置目标，主窗口切到对应标签，独立窗口直接前置。已关闭的标签由 `open` 重新打开。横幅复用 `/chrome` 前端入口、三主题 token 及中英文文案，仅其专用 preload 提供通知 IPC。

## 验证

```bash
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-11-14_桌面多视图 -p "test_navigation_v3.py"
/opt/homebrew/bin/python3.11 -B test/2026-10-23_桌面导航重构/run_regressions.py
node test/2026-11-14_桌面多视图/frontend/v3.cjs
node test/2026-11-14_桌面多视图/frontend/p2.cjs
```

离线回归不等同于真机验收；拖出、红绿灯、多显示器、输入法由用户验收。前端产物、后端模块与 Electron 壳同包发布，旧壳（无 `astrionShell`）加载新前端按网页端降级。
