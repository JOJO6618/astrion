# 桌面多视图标签与独立对话窗口（设计稿）

> 状态：**P1/P2 已实现**（后端 v3、ViewManager、前端视图与独立窗口；2026-11-14），P3 与真机验收未做。契约以 `docs/desktop_navigation.md` 为准，已知缺口见文末。实现前后如与代码冲突以代码为准，并回写本文、`AGENTS.md` §19 与 `docs/desktop_navigation.md`。
> 适用：Electron 桌面壳（macOS + Windows）。网页端（浏览器直接访问后端端口）与 Android 不启用本机制，但共用同一份前端产物，必须保持可用。

## 1. 目标与已确认决策

**目标**

1. 标签栏里的多个对话同时挂载、各自轮询，切换标签几乎无延迟（现状：activate → 全量 bootstrap → commit → 整表重建／重挂载，延迟明显）。
2. 独立页面（设置、工作流、Git）也做成标签，常驻。
3. 对话标签可拖出主窗口成为「独立对话窗口」，标签栏支持拖拽排序。

**架构选择：每个标签一个独立 `WebContentsView`（完整独立前端页面）**，而不是在一个 Vue 应用里同时持有多个对话。理由：现有前端约 300 个 App 字段＋十余个 Pinia store 都是「单个当前对话」形状（`stores/task.ts` 单一 `currentTaskId`/游标、`stores/chat.ts` 单一 `messages`、单一 `ChatArea` 实例）；改成单应用多对话等于重写显示快照（§19）全部所有权机制。独立视图下这些单例在每个页面内依旧成立，现有 bootstrap／任务事件游标链路原样复用；拖出窗口也只是把视图换个父窗口。后端 `GET /api/tasks/<task_id>?from=` 只按用户＋task_id 读取，本就支持 N 路并发，无需改动。

**已确认决策**

| # | 决策 |
|---|---|
| D1 | 仅桌面端启用；网页端保持单对话行为，但须兼容新后端协议 |
| D2 | 主窗口中每个对话标签（含空白新对话、Chat 标签）对应一个视图；**常驻上限 6 个**，超出按最近查看顺序淘汰最久未看的；当前显示的永不淘汰；淘汰只销毁视图，不停止任务、不关标签，再次激活走冷加载 |
| D3 | 独立页面标签：设置（全局一个）、工作流（全局一个）、Git（**每个工作区一个**）。打开一次即常驻、**不计入 6 个上限**，只有在标签栏关闭才销毁；再次点击对应按钮回到该标签（参考 Astrion 浏览器 `createInternal` 的按 section 去重逻辑） |
| D4 | 后端 `modules/desktop_navigation/` 仍是标签集合、顺序、归属（主窗口／独立窗口）的**唯一写者**；壳层切换已加载视图时先显示、后异步上报 |
| D5 | 一个对话全局只有一个标签；再次打开（侧栏点击等）定位到已有标签；若它在独立窗口则前置该窗口，不在主窗口打开 |
| D6 | 独立对话窗口 = 对话标题（顶部栏）＋系统三大键＋对话区，没有侧栏和标签栏 |
| D7 | 拖出后主窗口标签栏**不保留**该标签 |
| D8 | 关闭独立对话窗口 = 标签**放回**主窗口（追加到末尾，不抢占当前标签）；仍空白的 detached 新对话标签直接移除标签（无内容可放回） |
| D9 | 重启应用**恢复**独立对话窗口及其位置大小 |
| D10 | 拖出当前标签时，主窗口切到**右侧**相邻标签（无右侧则左侧），与现有关闭规则（`model.py remove_tabs`：`tabs[min(index, len-1)]`）一致；拖出主窗口唯一的对话标签时补空白新对话标签 |
| D11 | 主窗口标签栏支持拖拽排序；独立页面标签可排序不可拖出；对话标签与未绑定对话的空白新对话标签都可拖出 |
| D12 | 浏览器面板跟随对话所在窗口；浏览器独立窗口「收回到侧边栏」收回到**该对话当前所在的窗口**（独立对话窗口或主窗口） |

## 2. 术语

- **主窗口**：唯一带标签栏的完整窗口。布局 = 顶部 chrome 视图（46px，`/chrome`，标签栏）＋内容区。
- **内容视图**：一个标签对应的 `WebContentsView`，加载一个完整前端页面。分三类：对话视图（`conv`/`new`，Agent 或 Chat）、独立页面视图（`page`）。
- **独立对话窗口**：从主窗口拖出的单对话窗口，只含一个对话视图。
- **ViewManager**：Electron 主进程中新增的窗口／视图管理器，取代 `window.js` 里的 `mainView` 单例。
- **快照**：`GET /api/conversation-tabs` 返回的带 `revision` 的权威导航状态。

## 3. 后端导航模型 v3（`modules/desktop_navigation/`）

### 3.1 状态

```jsonc
{
  "version": 3, "revision": 12,
  "tabs": [
    { "key": "tab_…", "kind": "conv" | "new" | "page",
      "conversation_kind": "agent" | "chat",       // conv/new
      "conversation_id": "conv_…", "workspace_id": "…", "workspace_label": "…",
      "title": "…", "error": "",
      "page_type": "settings" | "workflows" | "git", // 仅 page
      "route": "/settings/models",                  // 仅 page：最近路由，用于恢复
      "opener_key": "tab_…",                        // 仅 page：关闭后的返回目标
      "placement": "main" | "detached" }
  ],
  "active_key": "tab_…",          // 主窗口当前标签（唯一权威）
  "workspace_id": "…",            // 最近一次激活 Agent 标签的工作区（零工作区判断沿用）
  "renderers": { "tab_…": "renderer-uuid" },   // 每标签当前文档身份
  "receipts": [...], "closed_keys": [...]
}
```

- **删除** `navigation`（pending/committed/error 目标）、`resume_key`、单一 `renderer_id`。原因：每个视图一生只绑定一个标签，不再存在「同一文档被改指向另一个目标」的过程，原来为它服务的 pending→commit 门禁失去意义。
- `tabs[]` 顺序即主窗口标签顺序；`placement=detached` 的标签保留在数组中但不在标签栏显示。`MAX_TABS=100` 保持。
- 去重：`conv` 按 `(conversation_kind, workspace_id, conversation_id)`；`page` 按 `page_type`（`git` 再加 `workspace_id`）。
- `renderers` 只记录内容视图；chrome 视图不需要 renderer 身份。

### 3.2 命令

沿用 `POST /api/conversation-tabs/commands` 的 envelope、receipt 幂等与 409 语义。

| 动作 | 发起方 | 行为 |
|---|---|---|
| `create` | 标签栏／页面 | 新建空白新对话标签（`placement=main`），追加并激活 |
| `open` | 页面（侧栏等） | 已存在：`main` 则激活，`detached` 则不改 active、返回 `{key, focus: "detached"}`；不存在：新建 main 标签并激活 |
| `workspace` | 页面（工作区切换） | 语义同现状（该工作区活动主任务或空页），之后同 `open` |
| `page` | 页面（设置/工作流/Git 按钮） | 按 `page_type`(+workspace) 找标签，没有就新建；更新 `route`；`opener_key` = 当前 active（不等于自身时）；激活 |
| `activate` | 标签栏 | 仅允许 `placement=main` 的标签 |
| `close` | 标签栏 | 移除；若是 active：`page` 标签优先回 `opener_key`（仍存在且在主窗口），否则右侧相邻、无则左侧；主窗口已无标签则补空白新对话标签 |
| `reorder` | 标签栏 | `{key, before_key}`，只调整主窗口标签相对顺序，不改 active |
| `detach` | 标签栏（拖出） | 仅 `conv`/`new`（页面标签不可）；置 `placement=detached`；若是 active 按 D10 选继任；主窗口无标签则补空白新对话标签 |
| `dock` | 标签栏（壳层转发窗口关闭） | 置 `placement=main` 并移到数组末尾；不改 active。仍空白的 detached 新对话标签直接移除标签 |
| `attach` | 内容视图 | `{key, renderer_id}`：登记 `renderers[key]`，覆盖旧文档（刷新后旧文档失去写权）。标签不存在返回 409 |
| `bind` | 内容视图 | 新对话首消息绑定；要求 `renderers[key] == renderer_id`，其余同现状 |
| `fail` | 内容视图 | 标记本标签 `error`（展示用）；要求 renderer 匹配；对话已删除则移除标签 |
| `title` / `deleted` / `relocated` | 同现状 | 不变 |
| `focus` | 标签栏（壳层转发窗口聚焦） | 只更新内存中的「最近聚焦标签」供 CrossPet 使用，不落盘、不增 revision |

删除：`commit`、`resume`（独立页面返回由 `page` 标签的 `opener_key` 与普通 `activate` 取代）。

### 3.3 迁移与校验

- v2 → v3 一次迁移：保留顺序，全部 `placement=main`；`active_key` 为空时取 `resume_key`，仍为空取首个标签；丢弃 `navigation` 与 `renderer_id`（文档刷新后重新 attach）。
- 结构校验沿用「损坏不当空状态覆盖」原则；新增字段同样强校验。
- GET 快照的 5 秒轻量核验（对话存在性、工作区可用性）与标签富化（`running`、`awaiting_human_approval`）保持。

### 3.4 相关后端联动

- `server/conversation_tabs.py`：CrossPet `focus_tab` 改为在 `activate/open/page/focus/detach/dock` 后按「最近聚焦标签」上报（主窗口 active 或聚焦中的独立窗口标签）。
- `server/browser_retention.py` / `modules/native_browser/retention.py`：「前台对话」= 主窗口 active ＋ 所有 `placement=detached` 的对话（原来只认已提交的 active 与已弹出浏览器）。
- 删除对话、对话迁移（`relocated`）对 detached 标签同样生效；删除时壳层随快照关闭对应独立窗口。

## 4. Electron 壳：ViewManager

### 4.1 结构

`desktop-electron/src/window.js` 拆为：

- `views/manager.js`：`ViewManager`，持有 `mainWindow`、`chromeView`、`views: Map<key, Entry>`、`detachedWindows: Map<key, BaseWindow>`、最新快照与 revision。
- `views/entry.js`：`Entry = { key, kind, view, window, lastShown, url }`。
- `views/detached-window.js`：独立对话窗口创建、标题同步、位置记忆（参考 `browser/popout-window.js` 的 `placement()`）。
- `views/layout.js`：主窗口内容区 bounds（`y = 46`）、独立窗口 bounds（`y = 0`，顶部栏由页面自己画）。

`window.js` 只保留导出兼容 API（`focusMainWindow`、`getMainView` → 改为 `getActiveView()` 等）。

### 4.2 视图 URL 与身份

- 对话：`/<id>?workspace_id=…&tab=<key>`；空白：`/new?workspace_id=…&tab=<key>`；Chat：`/chat/<id>?tab=<key>`、`/chat/new?tab=<key>`；页面：`<route>?…&tab=<key>`。
- 新 preload `preload-view.cjs`（由 `preload-main.cjs` 演化）在保留现有桥（`astrionBrowser`、`astrionWindow`、快捷设置）的基础上新增 `astrionShell`：
  - `identity()` → `{key, placement}`（主进程按 sender 返回，页面不得自报）；
  - `onTabsState(cb)`：接收壳层广播的快照；
  - `forwardTabsState(state)`：页面拿到更新的快照（命令响应）后转交壳层；
  - `onVisibility(cb)`：`{visible}`；
  - `focusMain()`：前置主窗口（独立窗口中点击设置等按钮后调用）。
- `preload-chrome.cjs` 新增 `astrionShell`：`show(key)`、`forwardTabsState(state)`、`dragTab({key, phase, screenX, screenY})`、`onRequestDock(cb)`、`onWindowFocus(cb)`。

### 4.3 快照驱动的收敛（reconcile）

1. 快照来源：chrome 视图照旧 500ms 轮询；任何页面执行命令拿到更新快照后经 `forwardTabsState` 转交。壳层只接受 revision 更大的快照，然后广播给所有视图（**内容视图不再各自轮询标签快照**）。
2. 收敛规则：
   - `active_key` 的视图不存在 → 创建并显示；存在 → `setVisible(true)`，其余主窗口视图隐藏；
   - 快照中已不存在的标签 → 销毁视图（独立窗口一并关闭）；
   - `placement=detached` 且没有独立窗口 → 创建窗口并迁入视图（重启恢复也走这里）；
   - `placement=main` 但仍在独立窗口 → 迁回主窗口（隐藏）；
   - 执行 LRU（4.4）。
3. 乐观切换：标签栏点击先 `astrionShell.show(key)`（壳层立即切换已存在的视图；不存在则立即创建并显示加载态），同时 HTTP `activate`；响应快照若与乐观结果不一致，以快照为准再收敛。

### 4.4 常驻上限（LRU）

- 计数范围：主窗口内 `kind ∈ {conv, new}` 的视图（含 Chat）。`page` 视图与独立窗口视图不计数、不淘汰。
- 超过 6 个时淘汰 `lastShown` 最早且非 active 的视图：`webContents.close()`，Entry 删除。
- 启动：先只创建 active 视图，空闲后按最近查看顺序逐个预热，直到上限（避免冷切换；预热顺序需持久化 `lastShown`，存 `userData/view-recency.json`）。
- 风险：冷加载需要完整加载前端产物与 bootstrap，可能比现状同页切换更慢，必须在 P0 实测；缓解手段为预热与 V8 code cache。

### 4.5 隐藏视图与轮询负载

- 壳层在显示／隐藏视图时通过 `astrionShell.onVisibility` 通知；不依赖 `document.visibilityState`（隐藏 `WebContentsView` 是否触发需 P0 实测）。
- 页面隐藏时：任务事件轮询降为约 1s；暂停 `/api/status` 5s 刷新、`ui/workspace.ts` 的 `/api/tasks` 索引、2.5s 运行态对账、Git／终端数等辅助刷新。重新可见时立即补一次。
- Chromium 每主机 6 并发连接由所有视图共享；首期可接受，P3 视需要新增批量轮询接口（`POST /api/tasks/poll`，传 `{task_id: from}` 映射）。
- Chromium 对后台视图的定时器节流不影响正确性：任务事件游标续接＋`window_start` 缺口时快照对账本就兜底。

### 4.6 拖拽排序与拖出

- 标签栏用 pointer 事件实现（现有标签栏只有窗口拖动区，标签本身需 `-webkit-app-region: no-drag`）。`pointerdown` 后 `setPointerCapture`，移动超过阈值进入拖拽。
- 标签栏内拖动：本地预览重排，松手发 `reorder`。
- 拖出是**浏览器式**的（状态机见 `tabReorder.ts` 的 `dragStep`）：指针离开标签栏垂直方向超过阈值（约 40px）的**瞬间**，chrome 发 `astrionShell.dragTab({phase:'tear', key, screenX, screenY, offsetX, offsetY})`，不等松手：
  1. 壳层（`ViewManager.tear`）立即在光标下创建独立窗口（指针落在窗口顶部栏的抓取点 offset 处）、**不聚焦**（`showInactive`，不打断 chrome 的事件流）、把该对话视图迁入（`removeChildView` → `addChildView`，不重载）；若它是 active，先按 D10 乐观显示右侧相邻视图（不移动键盘焦点）。此时**不**请求后端 detach；该标签挂一个无超时的待确认 detach，拖拽期间任何快照都不会把它拉回，显示的主窗口标签也保持不变。
  2. 拖拽中 chrome 以 rAF 节流发 `phase:'move'`，壳层 `setPosition(指针 − offset)`；若超过约 120ms 没收到 move（指针捕获失效的兜底），壳层改用 `screen.getCursorScreenPoint()` 以约 60Hz 驱动窗口位置。
  3. `phase:'drop'`（松手；`pointercancel`/`lostpointercapture` 视为在最后位置松手）：壳层聚焦窗口与其视图、记住位置，进入正常的待确认流程并发 `shell:request-detach`，chrome 发 `detach` 命令，快照回来后收敛；后端拒绝则超时后快照把视图迁回。
  4. `phase:'cancel'`（拖出态下指针回到主窗口标签条带，或按 Esc）：壳层把视图放回主窗口、销毁拖拽窗口（不走「关闭→dock」，不写位置记忆）、恢复拖前显示的标签；chrome 回到排序态，同一手势可继续排序或再次拖出。
  5. 兜底：拖出态下 30s 没有任何拖拽消息、拖拽窗口被点击获得焦点、chrome 视图崩溃，都按 drop 处理；应用退出中途不 dock、不丢标签（后端仍是 main）；拖出过程中标签被删除则窗口随之销毁。同一时刻只允许一次拖出，乱序或重复阶段被忽略。
- 条内视觉：拖出后该标签从标签栏消失、其余标签合拢（元素保留以维持指针捕获，不再有虚线占位）；取消时重新出现。
- 不可拖出：`page` 标签。对话标签与空白新对话标签（含 Chat）同规则；空白标签拖出后在独立窗口里写首条消息即正常 `bind`。
- 指针捕获：Chromium 层已用合成事件验证（新窗口 showInactive 出现在光标下后 chrome 仍持续收到 pointermove，`test/2026-11-14_桌面多视图/p0_electron/p2_capture.cjs`）；macOS `mouseDragged` 路由与 Windows `SetCapture` 的真实表现需实机验收，上面的光标轮询即为兜底。

### 4.7 独立对话窗口

- `BaseWindow`，窗口装饰复用 `platform.js` 的 `popoutChromeOptions()`（macOS 红绿灯悬浮、Windows Window Controls Overlay）。
- 只含一个对话视图，bounds 铺满；顶部栏由页面以「独立窗口布局」绘制（见 5.4）。
- 窗口标题 = 页面 `document.title`（对话标题），经 `page-title-updated` 同步。
- 位置大小按标签 key 记在 `userData/conversation-windows.json`（`moved`/`resized`/关闭时写 `getNormalBounds()`），新窗口与已开窗口重叠时错开、夹到屏幕工作区内。
- 关闭窗口：壳层立即把视图迁回主窗口（隐藏，保持存活，此后计入 LRU），并经 chrome 视图 `onRequestDock` 发 `dock` 命令（壳层主进程不直接发 HTTP 写命令，避免另建 CSRF 通道）。
- 聚焦窗口：经 chrome 视图转发 `focus` 命令（CrossPet）。

### 4.8 主窗口生命周期

- 主窗口 `closed`：非快捷模式退出应用（独立窗口随之关闭，`placement` 已持久化，下次启动恢复）；快捷模式下主窗口与所有独立窗口一起隐藏，`focusMainWindow()` 时一起恢复。
- `window-all-closed` 行为不变。
- `window:surface`：接受任一受管内容视图上报，统一着色主窗口、所有独立窗口及视图。
- 窗口控制 HTTP 桥（`/api/desktop/window/*`）只服务主窗口；独立窗口使用系统三大键，不走桥。
- 快捷入口 `quick/controller.js` 的可信判定从 `getMainView()` 改为「任一受管内容视图」；`openMain` 仍前置主窗口。

## 5. 前端改动（`static/src`）

### 5.1 视图身份与导航协调器

- 新增 `app/methods/ui/desktopView.ts` 取代 `desktopNavigation.ts` 的 `applyTarget` 协调器：启动时 `astrionShell.identity()` 取得 `{key, placement}`，`attach {key, renderer_id}`，此后**本页面不再切换到其他标签**。
- 侧栏点击对话、新建、工作区切换、打开设置等：一律发命令（`open`/`create`/`workspace`/`page`），响应快照 `forwardTabsState` 交壳层切换／前置。点击的就是本页对话时为空操作。
- 页面仍按 URL 走唯一 bootstrap（`conversation/bootstrap.ts`）加载自身对话；`desktopNavigation` 相关的 commit 步骤删除，其余快照／游标／所有权机制不变。
- `conversationTabs` store：字段改为 v3，`hydrate` 改为接收壳层广播（chrome 视图仍自己轮询）。
- 发送守卫 `isDesktopNavigationReady` 改为：已 attach ∧ `renderers[key]` 是本文档 ∧ 标签仍存在 ∧ 标签身份 = 可见 session 身份。
- 新对话首消息 `bind` 沿用发送前捕获的 key（即本视图 key）。
- 删除 `location.assign` 跨模式／独立页面整页跳转分支（Chat↔Agent、页面往返都变成另一个视图）。

### 5.2 隐藏态

- 新增 `ui/viewVisibility.ts`：订阅 `astrionShell.onVisibility`，提供 `isViewVisible()`；`stores/task.ts` 轮询间隔、`ui/socket.ts` 状态刷新、`ui/workspace.ts` 任务索引、`taskPolling/probe.ts` 对账按可见性调整。网页端恒为可见。

### 5.3 独立页面视图

- 设置／工作流／Git 页面在自己的视图中加载，不挂 Agent 运行态（沿用设置页独立挂载规则）。
- 页面内「返回」按钮 = `activate(opener_key)`（不关闭页面标签）；opener 已不存在时回右侧相邻标签。
- 页面内部子路由切换（如设置分区）就地 History API，不发命令；`route` 只在 `page` 命令时更新，用于恢复。

### 5.4 独立窗口布局

- `placement=detached` 时：隐藏侧栏、不预留 46px 标签栏；顶部绘制约 38px 栏（对话标题、`-webkit-app-region: drag`、按平台为红绿灯／WCO 留位）。仍空白的标签显示「新对话」（与标签栏一致）。
- 打开设置、Git、工作流等主窗口能力的按钮：发 `page` 命令后 `astrionShell.focusMain()`。
- 侧栏缺失导致不可达的功能（新建对话、切换对话）在独立窗口中不提供。

### 5.5 标签栏（`components/chrome/ConversationTabStrip.vue`）

- 只显示 `placement=main` 的标签；`page` 标签使用对应图标与固定文案（Git 带工作区名）。
- 拖拽排序与拖出（4.6）；关闭动画仍只是投影，命令点击即发。
- 点击：`astrionShell.show(key)` + `activate`。

### 5.6 网页端兼容

- 所有桌面分支以 `astrionShell` 存在为前提（不只看 `__ASTRION_DESKTOP__`）；网页端无此桥，标签 store `enabled=false`，行为与现状一致。
- 前端产物、后端模块、Electron 壳在同一安装包内发布，协议 v3 前后端一起升级；旧壳（无 `astrionShell`）加载新前端时按网页端单页逻辑降级运行。

## 6. 浏览器宿主多实例化（`desktop-electron/src/browser/`）

现状：`NativeBrowser.main` 是唯一主宿主（主窗口＋`mainView`），其 owner 随主页面当前对话变化；`windowFor(owner)` 只区分「已弹出 → 弹出窗口」与「其余 → 主窗口」；`undock` 一律挪回主窗口。

改为：

- 每个内容视图一个 `BrowserHost`（kind `view`），owner 由该视图对话决定且基本固定（`/new` 绑定时沿用 `adoptOwner` 迁移）；`NativeBrowser.hosts()` = 所有视图宿主＋弹出宿主。`check(event)` 按 sender 找宿主的逻辑不变。
- `windowFor(owner)`：浏览器已弹出 → 弹出窗口；否则 → 该对话视图当前所在窗口（ViewManager 提供：主窗口或独立对话窗口）；视图已被 LRU 淘汰 → 主窗口（隐藏停放，等同现状后台对话）。
- 对话视图迁移窗口（拖出、放回）时，ViewManager 通知 NativeBrowser 对该 owner 的所有网页视图重新 `attach`（不重载）并 `layout()`。
- `dock`（收回到侧边栏）：`undock` 后网页视图按新的 `windowFor(owner)` 挂回——对话在独立窗口就回独立窗口，在主窗口就回主窗口（D12）。
- `windowCommand` 的 `popout` 允许来自独立对话窗口中的视图宿主（现状只拒绝弹出宿主自身）。
- 浏览器弹出期间关闭独立对话窗口：弹出窗口保持；之后收回进主窗口。
- 回收前台集合按 3.4 更新；`boundsFor(owner)` 取当前宿主视图 bounds。
- 视图宿主被销毁（LRU 淘汰、标签关闭）时 `invalidate` 并解除监听，与现有 `render-process-gone` 路径一致。

## 7. 多页面共存的共享状态审查清单

多页面同时在线带来的竞态与「拖出」无关，主窗口 6 个视图并存时即已存在。实现 P1 前逐项审查并补回归：

| 共享点 | 现状 | 要求 |
|---|---|---|
| session 中的选中工作区（`host_workspace_id`） | `workspaceRequestScope.ts` 为同源 `/api/` fetch 自动注入可见工作区头；原生资源 URL 经 `withWorkspaceScope` 自带 `workspace_id` | 视图一律按 URL 中的显式工作区＋对话启动，**不读取 session 选择**；隐藏视图不调用选择接口；全量排查未带工作区头的请求（含 `workspaceStartup.ts` 首次进入） |
| 后端运行实例与模式 | `find_runtime` 按 用户::工作区::对话 区分；`modes.py` 校验绑定对话 | 复核 `/api/status` 未传 `conversation_id` 时读 base terminal 的 `current_conversation_id` 的使用点，视图应显式带对话 ID |
| 草稿 | 按 用户/工作区/对话 隔离，revision CAS | 不变；复核 localStorage 跨视图同 key 写入 |
| 标签快照 | 每个文档 500ms 轮询 | 仅 chrome 视图轮询，壳层广播 |
| 全局轮询 | 每页面各跑一份 | 按 5.2 挂起 |
| Markdown／展开态等模块级缓存 | 页面内单例 | 视图隔离后天然独立，无需处理 |
| 浏览器回收／CrossPet 焦点 | 只认单一 active | 按 3.4 改为多前台 |

## 8. 需同步修改的约束文档

- `AGENTS.md` §19「桌面标签导航」：唯一写者不变；删除「显示提交前校验 renderer/navigation 所有权」「刷新继承 pending 目标」等 v2 约束，改为每标签 renderer 所有权、壳层乐观显示、视图固定绑定标签。
- `docs/desktop_navigation.md`：重写为 v3（或在本文实现后合并）。
- `docs/native_browser.md`「单独窗口显示」：宿主模型改为多视图宿主，收回目标规则。
- `docs/agents_details/display_snapshot.md`：桌面标签协调段落。
- `AGENTS.md` 文档地图加入本文。

## 9. 分期

| 期 | 内容 | 交付判据 |
|---|---|---|
| P0 | 实测与基线：① 现状标签切换各段耗时；② 冷视图加载耗时与单视图内存；③ 隐藏 `WebContentsView` 的 `visibilityState`/定时器节流；④ 拖拽 pointer capture 越界行为（mac/win）；⑤ 视图跨 `BaseWindow` 迁移后页面状态保持（浏览器独立窗口已有 52 项迁移实验可复用） | 数据记录到 `test/2026-11-14_桌面多视图/README.md`，据此确认上限与预热策略 |
| P1 | 后端 v3 模型与迁移；ViewManager 单窗口多视图＋LRU＋预热；独立页面标签；拖拽排序；隐藏态降频；浏览器视图宿主多实例化（不含拆窗）；共享状态审查 | 主窗口内标签秒切；回归通过；用户视觉验收 |
| P2 | 拖出／放回、独立对话窗口布局、重启恢复、浏览器跟随与收回规则、CrossPet 与回收多前台 | 用户实机验收（mac 与 Windows） |
| P3 | 视负载新增批量轮询接口、内存调优 | 按 P0/P2 数据决定是否做 |

## 10. 测试

- 后端：`test/2026-11-14_桌面多视图/test_navigation_v3.py`——迁移、去重（含 page/git）、关闭继任（右优先、page 回 opener）、detach/dock/reorder、renderer 所有权、幂等 receipt；并改造 `test/2026-10-23_桌面导航重构/` 中依赖 commit/resume 的用例。
- 壳层：ViewManager 纯逻辑（收敛、LRU、乐观切换后被快照纠正）用伪 view 对象做 Node 单测；浏览器 `windowFor`/收回目标单测。
- 前端：发送守卫、隐藏态降频、视图身份启动（不读 session 选择）的定向测试；ESLint 与生产 build。
- 沿用 §19 运行快照、工作区切换、草稿回归。
- 视觉与真机交互（拖拽、红绿灯、多显示器、输入法）由用户验收；离线回归不等同于速度达标。

## 11. 未决事项（仍待用户决策）

- 快捷模式下关闭主窗口时独立窗口一并隐藏（4.8）为默认方案，实现前可再确认。
- 独立页面视图是否也需要内存上限（目前每类最多 1 个，Git 每工作区 1 个）。
- 冷加载若显著慢于现状，是否引入「备用空视图池」（预先加载应用壳、再注入身份），会重新引入身份后绑定逻辑，仅在 P0 数据不理想时考虑。

## 12. 已实现状态与已知缺口

已实现：后端 v3（迁移、page/detach/dock/reorder/focus、renderer 所有权）、浏览器回收多前台、ViewManager（LRU、预热、独立窗口）、前端视图 attach/发送守卫/隐藏降频、页面标签、拖拽排序与拖出、独立窗口布局、CrossPet 聚焦、`focusTab`/`onRequestPage` 桥。

已知缺口：
- 「未带工作区头的请求」排查：`/api` fetch 与原生资源 URL 已覆盖；已核：前端无 WebSocket/EventSource。
- 设置页内部跳转（如用量统计）就地 `location.assign`，页面标签 `route` 不随之更新；非设置页视图（如对话中的个人空间抽屉）改发 `page` 命令。
- 拖出指针越出窗口后的 pointer capture 行为、Windows 实机、多显示器未验证；拖拽 UI 无自动化测试。
- 任务轮询降频的 tick 门控只有纯函数与定时器层测试，隐藏 `WebContentsView` 的真实节流已在 P0 实测（默认 webPreferences 下 `visibilityState` 为 hidden、定时器约 1Hz，见 `test/2026-11-14_桌面多视图/p0_electron/README.md`）；降频门控本身只有单测。
- 批量轮询接口（P3）、独立页面视图内存上限、冷加载性能数据未做。
- `test/2026-11-08_浏览器无模型渲染` 已改 v3 但需 Electron 运行，未执行；`2026-10-08/09/10/16/17`、`2026-10-12` 前端套件在 HEAD 即失败（与本改动无关）。
