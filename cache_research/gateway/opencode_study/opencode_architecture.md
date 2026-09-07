# opencode server/client 架构研究报告

> 研究对象：opencode 仓库（版本 1.18.29，克隆位于 `<local-clones>/opencode`，只读）
> 研究目的：为 Astrion（Python Agent 项目）的 "gateway 化" 改造提供借鉴
> 研究方法：源码阅读，所有结论均附文件路径与关键代码证据（行号以本次阅读时为准）

---

## 总览（30 秒版）

opencode 有两个并存的 HTTP 服务层：

1. **主服务（packages/opencode，TUI 实际使用）**：Effect `HttpApi` 定义 + `HttpRouter.serve` 运行在 Node http server 上，路由分 Root（`/global/*`、`/control/*`）、Instance（`/session/*`、`/tui/*`、`/event` 等）、v2 protocol（`/api/*`）、Public（UI 静态资源）。OpenAPI spec 从 Effect HttpApi 定义**代码生成**（`bun dev generate` → `openapi.json` → `@hey-api/openapi-ts` → TS SDK）。
2. **精简实验服务（packages/server + packages/protocol）**：同样是 Effect HttpApi，把协议定义下沉到 `@opencode-ai/protocol`，server 只注入 middleware/handler 实现，挂载 `/api/event`、`/api/session/*` 等 v2 路由。

核心设计：**TUI 是 server 的瘦客户端**，通过生成的 SDK + SSE（`/global/event`、`/event`）消费事件；server 通过事件总线反向驱动 TUI（`/tui/*`）。

---

## 1. Server 架构：框架、入口、路由、OpenAPI、SDK 生成

### 1.1 用的是什么框架

**不是 Hono，也不是 Elysia，而是 Effect 生态的 `HttpApi` / `HttpRouter`**（Effect 自带的声明式 HTTP 框架，`effect/unstable/httpapi`），底层走 `@effect/platform-node` 的 `NodeHttpServer`（node:http）。

证据：

- `packages/server/src/api.ts`：`import { HttpApi, HttpApiGroup, HttpApiMiddleware, OpenApi } from "effect/unstable/httpapi"`，`HttpApi.make("server").add(...)` 组装所有 group。
- `packages/server/src/routes.ts`：`HttpApiBuilder.layer(Api, { openapiPath: "/openapi.json" })`；`webHandler()` 用 `HttpRouter.toWebHandler(...)` 导出 fetch handler。
- `packages/opencode/src/server/server.ts`：`HttpRouter.serve(HttpApiApp.createRoutes(opts), ...)` + `NodeHttpServer.layer(() => server, ...)`（createServer 来自 `node:http`）。
- 全局搜 `hono` 仅命中 server.ts 一处注释性匹配，无 Hono 依赖。

### 1.2 入口在哪、谁调用它

三个入口，同一套实现：

| 入口 | 文件 | 说明 |
|---|---|---|
| `opencode serve`（headless） | `packages/opencode/src/cli/cmd/serve.ts` | `Server.listen(opts)`，监听 `--port`（默认 4096）/`--hostname` |
| TUI 进程内嵌 worker | `packages/opencode/src/cli/tui/worker.ts` | worker 内 `Server.listen(input)`（`rpc.server` 方法）或纯内嵌 `Server.Default().app.fetch`（`rpc.fetch`） |
| 独立精简 server | `packages/cli/src/commands/handlers/serve.ts` | `HttpRouter.serve(createRoutes(password), ...)`，`opencode serve`（老 CLI） |

`packages/opencode/src/server/server.ts` 的 `listen()` 实现端口回退：`startWithPortFallback` 先试 4096，失败再随机端口。

### 1.3 路由如何组织

**分组（HttpApiGroup）+ 累积式组装**，两层：

- `packages/opencode/src/server/routes/instance/httpapi/api.ts`：
  - `RootHttpApi` = Control + ControlPlane + Global（`/global/*`）
  - `InstanceHttpApi` = Config/Experimental/File/Instance/Mcp/Project/Question/Permission/Provider/Session/Sync/Tui/Workspace（`/session/*`、`/tui/*`、`/permission/*` 等）
  - `OpenCodeHttpApi` = Root + Event(`/event`) + Instance + Server(`/api/*`) + PtyConnect(WS)
- 每个 group 一个文件，如 `groups/session.ts`、`groups/permission.ts`、`groups/tui.ts`、`groups/event.ts`；handler 对应 `handlers/*.ts`。

路由路径以组内常量定义（如 `groups/session.ts` 中 `SessionPaths = { permissions: "/session/:sessionID/permissions/:permissionID", ... }`）。

### 1.4 OpenAPI spec 是手写还是代码生成？

**纯代码生成**。Effect HttpApi 的每个 `HttpApiEndpoint` 用 `Schema` 声明入参/出参/错误，用 `OpenApi.annotations` 附加 summary/description/identifier，Effect 在运行时把整个 Api 编译成 OpenAPI 文档：

- `packages/opencode/src/server/server.ts`：`export async function openapi() { return OpenApi.fromApi(PublicApi) }`
- schema 定义集中在 `packages/schema/src`（zod 风格由 Effect `Schema` 实现，无 TypeBox/zod）：`session.ts`、`session-message.ts`、`session-event.ts`、`permission.ts`、`tui-event.ts` 等。
- 产物：`packages/sdk/openapi.json`（openapi 3.1.0，188 个 operationId）。

### 1.5 SDK 如何从 spec 生成

`packages/sdk/js/script/build.ts` 全流程：

1. `bun dev generate > openapi.json`：调用 CLI generate 命令（`packages/opencode/src/cli/cmd/generate.ts`）→ `Server.openapi()` 输出 spec，并给每个 operation 注入 `x-codeSamples`。
2. `@hey-api/openapi-ts`（`createClient`）生成：
   - `src/v2/gen/types.gen.ts`（TS 类型）
   - `src/v2/gen/sdk.gen.ts`（`OpencodeClient` 实例化 SDK，`paramsStructure: "flat"`）
   - `src/v2/gen/client/*`（fetch client，baseUrl 默认 `http://localhost:4096`）
3. 若干手工 patch（session.history 分页类型 string→number、SSE 泛型 bug）。
4. `bun prettier` + `bun tsc` 校验。

SDK 对外暴露：`packages/sdk/js/src/v2/client.ts`（`createOpencodeClient`，支持自定义 fetch、`x-opencode-directory` 头路由到指定目录），`packages/sdk/js/src/v2/server.ts`（`createOpencodeServer` 用 cross-spawn 拉起 `opencode serve` 子进程）、`process.ts`。

### 对 Astrion 的借鉴意义

- 如果 Astrion 也要"HTTP API + 生成 SDK"，可以用类似双轨：自己手写或代码生成 OpenAPI v3.1，再接入 openapi-typescript / openapi-generator / hey-api 生成 TS SDK（Python 侧可用 openapi-python-client）。
- "协议包（protocol）与实现（server）分离 + handler 注入"的分层（`@opencode-ai/protocol` 定义 group 与 error，`@opencode-ai/server` 注入 middleware/handler）值得借鉴，便于多入口复用同一协议。
- OpenAPI 元数据（summary/description）直接写在 schema 附近，文档与代码同源，避免过期。

---

## 2. 事件系统：/event、/global/event、事件总线、序号与重连

### 2.1 两条事件通路

**A) 全局总线 `/global/event`（RootHttpApi，GlobalApi）**

- 总线本体：`packages/opencode/src/bus/global.ts` —— 单例 Node `EventEmitter`（`GlobalBus.emit("event", {...})`），事件带 `directory/project/workspace/payload`。
- SSE 出口：`packages/opencode/src/server/routes/instance/httpapi/handlers/global.ts` 的 `eventResponse()`：`Stream.callback` 把 `GlobalBus.on("event")` 转成 Effect Stream，先发 `server.connected`，10 秒心跳，`Stream.pipeThroughChannel(Sse.encode())`。
- 事件源：`packages/opencode/src/event-v2-bridge.ts` —— `events.listen(...)` 把 core EventV2 的每个事件转发到 GlobalBus（`payload: {id, type, properties: data}`），durable 事件额外发一条 `{type:"sync", syncEvent:{...}}`。

**B) 实例流 `/event`（InstanceHttpApi，EventApi）**

- `packages/opencode/src/server/routes/instance/httpapi/handlers/event.ts`：`events.listen` 全量订阅 EventV2，然后按 `event.location.directory === instance.directory` **在服务端过滤**（`WorkspaceRoutingMiddleware` 用 `directory` query/`x-opencode-directory` 头选中实例），发 `server.connected` + 10s 心跳，遇 `server.instance.disposed` 关闭流。

**C) v2 精简流 `/api/event`（packages/server）**

- `packages/server/src/handlers/event.ts`：`EventV2.allBounded(events, 256)`（有界 dropping 队列，容量 256，溢出即断开报错），发 `server.connected`（类型由 `OpenCodeEvent` union 合一），15s 心跳。

### 2.2 事件类型

由 schema 中 `Event.define` 声明，按 manifest 汇总（`packages/schema/src/event-manifest.ts`）：

- **session 事件（v2 增量式）**：`packages/schema/src/session-event.ts` —— `session.next.prompted / prompt.admitted / context.updated / synthetic / shell.started|ended / step.started|ended|failed / text.started|delta|ended / reasoning.* / tool.input.*|called|progress|success|failed / retried / compaction.* / revert.*` 等约 30 种。Delta 类事件（text.delta、reasoning.delta、tool.input.delta）是 **live-only、不入库**；Ended 类是 replayable 边界。
- **v1 事件（面向现有 TUI）**：`packages/schema/src/v1/session.ts` 的 `PartDelta`/`MessageUpdated` 等 + `permission.asked/replied`（v1）与 `permission.v2.asked/replied`（v2）、`question.asked/replied`、`tui.*`（`tui-event.ts`）、`server.connected`（`server-event.ts`）、插件/集成事件等。
- 事件体统一形状：`{ id: "evt_xx", type, data/properties, location?, durable?: {aggregateID, seq, version}, metadata? }`（`packages/schema/src/event.ts`）。

### 2.3 序号 / offset / 重放语义

**核心机制在 `packages/core/src/event.ts`（EventV2 service）+ `packages/core/src/event/sql.ts`（SQLite）：**

- 表 `event_sequence(aggregate_id PK, seq, owner_id)` 与 `event(id PK, aggregate_id, seq, type(版本化), data JSON)`，`uniqueIndex(aggregate_id, seq)`。
- `publish()`：若是 durable 事件 → **事务内** 更新 seq（`latest+1`）、先跑 projectors（`project` 注册的投影回调，和写库同事务）、再插 EventTable；非 durable 事件只走内存 PubSub。
- 事件有**每聚合（per-aggregate，如 sessionID）单调 seq** 与 **version（schema 演进版本）**；type 落库时是版本化串（`EventV2.versionedType(type, version)`）。
- `durable({aggregateID, after})` 流：先 `readAfter(aggregateID, after)` **从 SQLite 重放 seq > after 的历史事件**，再 `Stream.concat` 内存 pubsub 的实时事件（`subscribeDurable` 用 per-aggregate 的 sliding PubSub 唤醒读库）。→ **断线重连 = 重放历史 + 续传实时**，这是"重放事件"路线。
- v1 老的那套 `/global/event` 与 `/event` 是 **live-only、无 seq 无重放**。

**对外的重放/追平接口（v2）**：

- `GET /api/session/:sessionID/event?after=N` → `StreamSse(SessionEvent.Durable)`，"Replay durable events after an aggregate sequence, then continue with new durable events"（`packages/protocol/src/groups/session.ts`）。
- `GET /api/session/:sessionID/history?limit&after` → 分页读 durable 事件（SessionHistory）。
- 多 agent 协同：`/sync/replay`、`/sync/history`（`packages/opencode/src/server/routes/instance/httpapi/groups/sync.ts`）返回 `{ aggregateID: lastKnownSeq } → seq 之后的事件`，用于客户端从同步点追平。

### 2.4 客户端断线后怎么追数据

- **TUI 主路径（v1 事件）**：`packages/tui/src/context/sdk.tsx` 里 `startSSE()` 调 `sdk.global.event()`，断了之后**指数退避重连**（1s→30s），**不重放** —— 因为 `/global/event` 无历史；TUI 靠**重新拉取状态**补齐：重连后重新 GET session/messages/status，并（实验性）`sync.start()` 开启工作区同步。
- **v2 路径（durable 事件）**：带 `after` seq 的订阅天然支持重放续传（2.3）。
- 结论：**"轻事件总线（全量广播、可丢、无 seq）+ 有 seq 的 durable 事件（SQLite 持久化、可重放）+ 客户端主动拉状态" 三层组合**，视客户端对可靠性的要求选层。

### 对 Astrion 的借鉴意义

- Python 侧实现 SSE 事件总线很简单（asyncio Queue + EventEmitter 等价物），关键是**给事件加 durable 序号并落库**，提供 `after=N` 重放订阅，才能让弱网/多客户端可靠追平。
- 把"live-only delta"与"durable 终值"分离（text.delta 不入库、text.ended 入库可重放）是很好的降本设计。
- 服务端按 `directory/workspace` 过滤事件（而非客户端过滤），减少带宽，Astrion 可按 workspace/agent 维度订阅。

---

## 3. Session 状态归属：存储、真状态、消息/part 模型

### 3.1 存哪

**SQLite（bun:sqlite + drizzle-orm + Effect 封装）**，单库文件：

- `packages/core/src/database/database.ts`：`PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000; foreign_keys=ON`，库文件 `Global.Path.data/opencode.db`（`opencode-${channel}.db`）。
- 表都在 `packages/core/src/session/sql.ts`：`session`、`message`、`part`、`session_message`（v2 投影）、`session_input`、`todo`、`session_context_epoch`；事件表在 `packages/core/src/event/sql.ts`。
- 老 v1 的会话（TUI 现行 API 用的 `SessionService`）读写 SQLite 的 `session/message/part` 表；新的 v2 由 `SessionProjector`（`packages/core/src/session/projector.ts`）把 durable 事件投影进 `session_message` 表。

### 3.2 谁拥有"真状态"

**Server（core 进程内）**：Session（`packages/opencode/src/session/session.ts` + `packages/core/src/v1/session`）和 SessionV2（`packages/core/src/session`，事件溯源风格）都在 server 进程的 Effect 服务里；**消息/part 以 SQLite 为持久真源，事件总线只广播变化；客户端（TUI/SDK）是只读投影**。v2 的"真状态"本质是 durable 事件日志（SQLite），投影表/API 视图都是派生物 —— 事件即真相。所有权通过 `EventSequenceTable.owner_id` 表达（`claim(aggregateID, ownerID)`，`replay` 支持 `strictOwner`），多进程共享 session 时只能有一个 owner 追加事件。

### 3.3 数据模型长什么样

- **v1 message**（`packages/schema/src/v1/session.ts`）：`MessageID = "msg_..."`，message = `{ id, sessionID, time:{created}, role, ... }`；**Part** 是 tagged union：`Text / Subtask / Reasoning / File / Tool / StepStart / StepFinish / Snapshot / Patch / Agent / Retry / Compaction`（`export const Part = Schema.Union([...])`，discriminator "type"）。SQLite `message.data`/`part.data` 以 JSON 存。
- **v2 session_message**（`packages/schema/src/session-message.ts`）：`SessionMessage.ID = "msg_..."`，tagged union：`agent-switched / model-switched / user / synthetic / system / shell / step-start / step-finish / reasoning / text / tool / ...`，带 `time:{created}`、`metadata`。投影表 `session_message(id, session_id, type, seq, data JSON)`，`uniqueIndex(session_id, seq)` —— **消息有每会话 seq，和事件 seq 对应**。
- **v2 session_input**（`packages/core/src/session/input.ts` + `sql.ts`）：输入先 `admitted_seq` 落库（durable admit），再 `promoted_seq` 被 agent 循环取走 —— 输入也是持久化的。

### 对 Astrion 的借鉴意义

- "事件日志为真相 + 投影表 + 视图 API"的 CQRS/事件溯源结构在 Python 侧可用 `sqlite3`/`SQLAlchemy` + 事件表（aggregate_id, seq, type, data）实现，成本可控。
- 文件级 Session 与项目隔离（`project_id`/`directory` 列）对 Astrion 多 agent、多工作区是现成参考。
- 输入 admit/promote 两段式（先持久化再消费）能天然解决"请求丢失/重复"问题。

---

## 4. 权限 / 审批流：`POST /session/:id/permissions/:permissionID`

### 4.1 路由在哪

当前主服务的定义在 `packages/opencode/src/server/routes/instance/httpapi/groups/session.ts`：

```ts
SessionPaths = {
  ...
  permissions: `${root}/:sessionID/permissions/:permissionID`,   // root = "/session"
}
```

handler 在 `packages/opencode/src/server/routes/instance/httpapi/handlers/session.ts`：

```ts
const permissionRespond = Effect.fn("SessionHttpApi.permissionRespond")(function* (ctx) {
  yield* requireSession(ctx.params.sessionID)
  yield* permissionSvc.reply({ requestID: ctx.params.permissionID, reply: ctx.payload.response })
    .pipe(Effect.catchTag("Permission.NotFoundError", ...))
  return true
})
```

（repo 中也存在 v2 等价路径 `POST /api/session/:sessionID/permission/:requestID/reply`，见 `packages/protocol/src/groups/permission.ts`。）

### 4.2 审批请求如何产生

审批服务于两代实现，机制相同（pending map + Deferred 阻塞 + 事件广播）：

- **老 v1**：`packages/opencode/src/permission/index.ts` —— `ask()` 先对 `ruleset + approved` 求值（`evaluate(permission, pattern, ...rulesets)`，`Wildcard.match` 匹配 rule：allow/deny/ask 默认 ask）；需要问人时创建 `PendingEntry{info, deferred}` 放入内存 `pending: Map<ID, PendingEntry>`，然后 `events.publish(Event.Asked, info)` 广播，最后 `Deferred.await(deferred)` **阻塞住 agent 的工具调用**直到有人回复。
- **新 v2**：`packages/core/src/permission.ts` —— `PermissionV2.ask()`（返回 `{id, effect}`）与 `assert()`（阻塞式，供 agent 内部用）：问人时 `create(request, agent)` 把 `{request, agent, deferred}` 放进 `pending: Map<ID, Pending>` 并 `events.publish(Event.Asked, request)`。

### 4.3 如何推给客户端

**通过事件总线广播，不是 HTTP 推送**：

- `events.publish(Event.Asked, info)` → `EventV2Bridge`（`packages/opencode/src/event-v2-bridge.ts`）→ `GlobalBus.emit("event", ...)` → `/global/event` SSE 推给所有已连接客户端。
- TUI 侧：`packages/tui/src/context/permission.tsx` / `feature-plugins/system/notifications.ts` 监听 `permission.asked` 事件弹审批 UI；同时 `context/sync.tsx` 的 case `permission.asked`/`permission.replied` 更新本地状态。

### 4.4 多个客户端同时连接时审批路由给谁

**不做"路由"，做"共享待办 + 先到先得"**：

- pending 表在 **server 进程内存**（`Map<ID, Pending/PendingEntry>`），所有客户端共享；
- `permission.asked` 广播给**所有** SS E 连接（TUI、web、其他 SDK 客户端都能看到并都能回复）；
- 回复接口按 `sessionID + requestID` 定位 pending 项（`reply()` 里 `pending.get(input.requestID)`），任何客户端 POST 都算；`Deferred` 只能被 resolve/fail 一次，后到的回复找不到请求（404 PermissionNotFoundError）；
- 不存在"审批钉死给某个 client"的机制 —— **谁先回复谁生效**。当多个 TUI 同时开着，`requestID` 是共享主键，service 层不区分连接。
- 附加：回复 `"always"` 时保存规则到 `permission_saved` 表（`packages/core/src/permission/saved.ts` + `sql.ts`），并把其他 pending 的同类问题自动放行；`"reject"` 会级联拒绝同 session 所有 pending。

### 对 Astrion 的借鉴意义

- 审批 = "异步请求对象（内存/DB）+ 阻塞等待（Deferred/Promise）+ 事件广播（总线）+ 客户端主动回复（HTTP POST）"，这是 HTTP 世界做 human-in-the-loop 的最小可靠模型。
- **多客户端审批共享同一请求 ID、先到先得** 意味着 Astrion 不需要做"连接路由"，只需保证请求对象全局唯一、回复幂等。
- 用**事件（permission.asked）驱动 UI、用 REST 回复**的做法，比 RPC 回调更解耦，值得在 gateway 中复用。

---

## 5. TUI 与 server 的关系：连接方式与 /tui/* 反向控制

### 5.1 TUI 进程如何连接 server

**两种模式**（`packages/opencode/src/cli/cmd/tui.ts`）：

1. **内嵌 worker 模式（默认）**：CLI handler 用 `new Worker(file, {...})` 拉起 `packages/opencode/src/cli/tui/worker.ts` 作为 **Bun worker 子进程**；worker 内 `Server.Default().app.fetch` 直接处理请求（不监听端口，URL 伪装成 `http://opencode.internal`）：
   - HTTP 请求走 RPC：`createWorkerFetch(client)` → `client.call("fetch", {...})` → worker `rpc.fetch` 调 `Server.Default().app.fetch(request)` 返回 Response；
   - 事件走 RPC 事件：`createEventSource(client)` → `client.on("global.event")` 把 worker 里 `GlobalBus.on("event")` 转发出来的事件喂给 TUI 的 EventSource 接口。
2. **外部模式（`--port`/`--hostname`/`--mdns`）**：先 `client.call("server", network)` 让 worker 真正 `Server.listen()` 起 HTTP 端口，TUI 用真实 URL + `Authorization` 头（`ServerAuth.headers()`，`packages/opencode/src/server/auth.ts`，`OPENCODE_SERVER_PASSWORD`）直连。

TUI 内全部通过**生成的 SDK**（`@opencode-ai/sdk/v2` 的 `createOpencodeClient`）访问 API，`packages/tui/src/context/sdk.tsx` 里 `startSSE()` 调 `sdk.global.event()` 开 SSE（见 §2.4 的重连逻辑）。

### 5.2 /tui/* 反向控制接口的设计意图

定义：`packages/opencode/src/server/routes/instance/httpapi/groups/tui.ts`（`TuiPaths`：`/tui/append-prompt`、`open-help`、`open-sessions`、`open-themes`、`open-models`、`submit-prompt`、`clear-prompt`、`execute-command`、`show-toast`、`publish`、`select-session`、`control/next`、`control/response`）。

**意图：把 TUI 当作一个可被 server 及任何客户端（web、插件、MCP、Agent）控制的"界面设备"**，实现方式不是私有的进程内回调，而是**两条标准通道**：

1. **事件通道（多数 /tui/* 端点）**：handler（`handlers/tui.ts`）并不直接调 TUI 内部函数，而是 `events.publish(TuiEvent.PromptAppend / CommandExecute / ToastShow / SessionSelect, ...)` **把"UI 指令"作为普通事件发布到事件总线**；TUI 作为 SSE 消费者收到 `tui.toast.show`、`tui.command.execute`（command 恒为 `session.list`、`help.show`、`model.list` 等字符串命令）后自己执行弹窗/切换。例如：
   - `openHelp` → `publishCommand("help.show")`
   - `openSessions` → `publishCommand("session.list")`
   - MCP 认证失败时 server 自己 `events.publish(TuiEvent.ToastShow, {title:"MCP Authentication Required", ...})`（`packages/opencode/src/mcp/index.ts`）—— 同一通道、任意调用方。
2. **请求/响应队列通道（`/tui/control/next` + `/tui/control/response`）**：`packages/opencode/src/server/shared/tui-control.ts` 用两个模块级 `AsyncQueue`（`packages/opencode/src/util/queue.ts`，阻塞式队列）实现 `submitTuiRequest({path, body})` / `nextTuiRequest()` / `submitTuiResponse(body)` / `nextTuiResponse()`；TUI 拉取 `control/next`（long-poll）执行、POST `control/response` 交回结果。适合"必须拿到返回值"的 UI 操作（如 TUI 弹一个选择框，server 等它的结果）。

**结论**：`/tui/*` = "通过标准 HTTP 接口把事件塞进总线、由 TUI 自主消费"，使 TUI 与 server 彻底解耦 —— 同一台 server 可以同时被 TUI、桌面端、web 端、Agent 进程控制，且 UI 指令事件天然对所有客户端可见。

### 对 Astrion 的借鉴意义

- "UI 即客户端设备、指令走事件总线、回执走请求队列"是 gateway 化后"远程控制本地交互界面"的标准答案：Astrion 的"弹确认框/提示"可以由任意调用方发布指令事件，前端订阅执行。
- 内嵌 worker + RPC 屏蔽 HTTP 与进程内调用的差异（`rpc.fetch` 模式），方便单元测试与本机零端口运行；对外则暴露真实端口 + 密码认证。Astrion 可在"进程内 gateway"与"独立 gateway 服务"之间无缝切换。

---

## 6. 实例模型：单实例单项目 vs 多项目多会话

**是"单 server 进程 + 多 project 实例 + 每目录懒加载"**。

- **instance 概念**：`packages/opencode/src/project/instance-store.ts` 里 `InstanceStore.load({directory, ...})` —— 每个**目录（directory）**是一个 `InstanceContext {directory, worktree, project}`，server 进程用 `cache: Map<string, Entry>` 按目录缓存懒加载的实例；实例内包含一套完整 core 服务（Session/Permission/EventV2 等 Effect 层，`AppNodeBuilder` 组装）。
- **路由如何选实例**：`WorkspaceRoutingMiddleware`（`middleware/workspace-routing.ts`）读 query 的 `directory`/`workspace` 或 header `x-opencode-directory` → `InstanceContextMiddleware`（`middleware/instance-context.ts`）`store.load({directory})` 把请求路由到该目录的实例上下文（`InstanceRef`）。SDK 客户端可在创建时传 `directory`（`packages/sdk/js/src/v2/client.ts` 自动加 `x-opencode-directory` 头）。
- `opencode serve` 的注释直接说明："Server loads instances per-request via x-opencode-directory header — no need for an ambient project InstanceContext at startup."（`packages/opencode/src/cli/cmd/serve.ts`）
- **workspace（实验性）**：`WorkspaceV2`（control-plane 的 `workspace.ts`）在 directory 之上再加一层，`WorkspaceRouteContext {directory, workspaceID}`；事件按 `location.directory + workspaceID` 过滤。
- 会话（session）挂在 project（`SessionTable.project_id`）下，同一 server 可同时服务多个 project/session；每个项目有自己的 SQLite 数据（同库分目录），隔离靠 directory/project 列与实例上下文。
- 多实例并发时，单写者原则由 `EventSequenceTable.owner_id` + `claim`/`strictOwner` 保证（§3.2）。

### 对 Astrion 的借鉴意义

- Astrion gateway 可以是"单进程多 workspace 懒加载实例"而非"一项目一进程"：进程常驻、按请求头路由实例上下文，大幅简化部署；实例级状态（session、事件流）天然隔离。
- 用请求头/query 选实例的方案（`x-opencode-directory`）可作为 Astrion 多租户路由的样板。

---

## opencode 设计要点速查表

| # | 要点 | 一句话 |
|---|---|---|
| 1 | 框架 | Effect `HttpApi/HttpRouter`（非 Hono），Node http 底层，`OpenApi.fromApi` 代码生成 OpenAPI 3.1 |
| 2 | SDK | `bun dev generate` → openapi.json → `@hey-api/openapi-ts` → TS SDK（fetch client + SSE 类型） |
| 3 | Server 分层 | `@opencode-ai/protocol`（协议/group） + `@opencode-ai/server`（middleware/handler 注入） + `@opencode-ai/core`（领域服务） |
| 4 | 事件总线 | `GlobalBus`（EventEmitter）→ `/global/event` SSE（全量广播、live-only、10s 心跳）；`/event` 按 directory 过滤 |
| 5 | 可靠事件 | EventV2：SQLite `event/event_sequence` 表 + per-aggregate seq + version；`durable(after)` 先重放后续传 |
| 6 | session 真状态 | Server 进程 + SQLite（WAL）；消息/part 是 JSON 投影（v1 message/part 表、v2 session_message 表），事件日志即真相 |
| 7 | 消息模型 | Message/Part 都是 taggged union（type 判别），v2 session_message 带 per-session seq |
| 8 | 审批流 | pending Map + Deferred 阻塞等待 + `permission.asked` 事件广播；按 requestID 先到先得回复，无连接路由 |
| 9 | TUI 关系 | TUI=瘦客户端：内嵌 Bun worker + RPC fetch/事件，或 `--port` 真 HTTP + 密码认证；`/tui/*` 把 UI 指令发布为事件、TUI 自主消费 |
| 10 | 实例模型 | 单进程多项目：按 directory/`x-opencode-directory` 头懒加载 InstanceContext，实例内一套 core 服务；单写者由事件 ownership 保证 |