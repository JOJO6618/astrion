# openclaw Gateway 架构研究报告

> 研究对象：openclaw 源码仓库（只读），用于为 Python Agent 项目 **Astrion** 的 "gateway 化" 改造提供借鉴。
> 研究方式：以文档要点为线索，直接在源码中定位实现并给出佐证（文件路径 + 关键代码）。
> 所有路径相对仓库根 `<local-clones>/openclaw`。

---

## 1. Gateway 进程结构

### 结论
单一长驻守护进程：`openclaw.mjs`（二进制入口）→ CLI 路由 `gateway` 命令 → `startGatewayServer()` 拉起 HTTP/WS 服务。WS server 用 `noServer: true` 模式挂在 HTTP server 上，由统一的 upgrade 路由分配连接归属（core WS / 插件 / worker / desktop 流）。每条连接由一个 `ws-connection.ts` handler 全权管理（预认证预算、connect 握手门禁、消息分发、keepalive、慢消费者关闭），连接注册进全局 `GatewayClientRegistry`（一个 `Set<GatewayWsClient>`）。方法分发通过 `GatewayMethodRegistry`（方法名 → handler/scope 的路由表）。

### 关键文件与代码证据

**入口链：**
- `openclaw.mjs` → `src/entry.ts`（`runCliWithExitFinalization`）→ `src/cli/run-main.ts`（`arg !== "gateway"` 分支，L130；`import("./gateway-cli/run-command.js")` L182）→ `src/cli/gateway-cli/run.ts`（L687 `const { startGatewayServer } = await loadServerModule()`；L1131 `return await startGatewayServer(port, {...})`）。
- `src/gateway/server.ts`：公开入口，默认端口 `18789`，动态 import 真正的实现：
  ```ts
  export async function startGatewayServer(port = 18789, opts: GatewayServerOptions = {}) {
    const mod = await loadServerStart();
    return await mod.startGatewayServerCore(port, { ...opts, startupStartedAt });
  }
  ```
- `src/gateway/server-start.ts`：`startGatewayServerCore()` → `createGatewayKernel(port, opts)`（`server-kernel.ts`）→ `createGatewayHttpTransport()`（`server-runtime-state.ts`）→ `finishGatewayStartup()`（`server-startup-finish.ts`，在其中 `attachGatewayWsHandlers(...)`，L147-170）。

**WS server 组织：**
- `src/gateway/server-runtime-state.ts` L303-304：`const wss = new NpmWebSocketServer({ noServer: true, ... })`，HTTP server 的 `upgrade` 事件统一路由。
- `src/gateway/server-http-upgrades.ts`：`httpServer.on("upgrade", ...)`（L221）按路径/来源分派；core 路径最终 `wss.handleUpgrade(req, socket, head, (ws) => { wss.emit("connection", ws, req); })`（L163-167）。
- `src/gateway/server/ws-connection.ts`：`attachGatewayWsConnectionHandler()`（L121）注册 `wss.on("connection", ...)`，每连接：
  - 生成 `connId = randomUUID()`；预认证预算 `preauthConnectionBudget`；握手超时 `resolvePreauthHandshakeTimeoutMs`；
  - keepalive：`src/gateway/websocket-keepalive.ts` —— 每 25s `socket.ping()`，错过 pong 判死；
  - `send(obj)` 检查 `socket.bufferedAmount > MAX_BUFFERED_BYTES`（50MB）拒绝/关闭；
  - 慢消费者：`server-broadcast.ts` 中 `slow && opts?.dropIfSlow` 则丢弃但**消耗 seq**；否则 `close(1008, "slow consumer")`。
- 连接注册表：`src/gateway/server/client-registry.ts` —— `class GatewayClientRegistry extends Set<GatewayWsClient>`，支持按 connId 索引。
- 连接状态/存在感：`src/gateway/server/client-presence.ts`、`presence-events.ts`（presence 快照广播）。

**帧校验与分发路由表：**
- 首帧门禁：`src/gateway/server/ws-connection/message-handler.ts`（`handleMessage`）——未认证连接只接受 `{type:"req", method:"connect"}` 且 `validateConnectParams` 通过，否则 `close(1008, "invalid handshake")`；成功后进入 `authenticated-request-dispatch.ts`。
- 方法路由表：`src/gateway/methods/registry.ts` —— `createGatewayMethodRegistry(inputs)` 把描述符（含 handler）存 `byName: Map<string, GatewayMethodDescriptor>`，重复方法名直接抛错；`getHandler(name)` / `getScope(name)` 供分发使用。核心方法策略表在 `src/gateway/methods/core-descriptors.ts`（`CORE_GATEWAY_METHOD_SPECS`，每行 `[name, family, scope, since, policy]`）。
- 分发执行：`src/gateway/server-methods.ts` 的 `handleGatewayRequest` —— `authorizeGatewayMethod()`（role/scope 校验，L148 起）→ 注册表取 handler → 执行；请求进入时先 `validateRequestFrame`，非法即回 `res {ok:false, error}`。
- 方法组装：`src/gateway/server-core-runtime.ts` L407 `createGatewayMethodRegistry(...)` 汇聚 core handlers + `extraHandlers`；`server-startup-finish.ts` 把 `getMethodRegistry`、`gatewayMethods`（方法名清单）、`events`（事件清单）注入 WS 层，hello-ok 里下发给客户端。

### 对 Astrion 的借鉴意义
- 用 "HTTP server + WebSocketServer(noServer) + 统一 upgrade 路由" 的组织方式，天然支持未来把插件/worker/控制面放在不同 WS 路径上，且共享同一生命周期（shutdown drain、连接预算）。
- 方法路由表（名称 → handler → scope → since）与 handlers 分离注册，是 Astrion 在 Python 侧可以照抄的骨架：一个 `@method("sessions.send", scope="write")` 式装饰器注册表，启动时查重。
- 预认证预算 + 慢消费者策略（dropIfSlow 或 close 1008）值得直接抄：单网关面对大量控制面客户端时这是稳健性的关键。

---

## 2. 协议实现（connect / req / res / event / seq / stateVersion / 重连恢复 / 事件不重放）

### 结论
- 线协议：WS 文本帧 JSON；**首帧必须是 `connect` 请求**；服务端在连接建立后先推送 `connect.challenge` 事件（含一次性 nonce），客户端用设备 Ed25519 私钥签名 nonce 后再发 connect。
- `req/res` 配对靠客户端生成的 `id`；`event` 帧带 `seq`（每连接单调，服务端生成）与可选 `stateVersion`（presence/health 两个单调计数器）。
- **事件不重放**：服务端不缓存事件；客户端靠 seq 间隙检测（`seq > lastSeq+1`）触发 `onGap`，UI 的做法是直接重连，重连后通过 hello-ok 拿到**全量 snapshot**。重连期间 `lastSeq` 被重置为新 generation。
- `stateVersion` 用于"有缺口就刷新状态"：hello-ok 的 snapshot 带 `stateVersion`，事件帧可选携带，客户端可据此判断 presence/health 子树是否过期并定向刷新。

### 关键文件与代码证据

**帧 schema（TypeBox）：** `packages/gateway-protocol/src/schema/frames.ts`
```ts
export const RequestFrameSchema = closedObject({
  type: Type.Literal("req"), id: NonEmptyString, method: NonEmptyString,
  params: Type.Optional(Type.Unknown()), traceparent: Type.Optional(...),
});
export const ResponseFrameSchema = closedObject({
  type: Type.Literal("res"), id: NonEmptyString, ok: Type.Boolean(),
  payload: Type.Optional(Type.Unknown()), error: Type.Optional(ErrorShapeSchema),
});
export const EventFrameSchema = closedObject({
  type: Type.Literal("event"), event: NonEmptyString, payload: Type.Optional(Type.Unknown()),
  seq: Type.Optional(Type.Integer({ minimum: 0 })),
  stateVersion: Type.Optional(StateVersionSchema),
});
```
`ConnectParamsSchema`（同文件）含 `client{id, version, mode, ...}`、`device{id, publicKey, signature, signedAt, nonce}`、`auth{token, bootstrapToken, deviceToken, password, ...}`；`HelloOkSchema` 含 `features{methods, events}`、`snapshot: SnapshotSchema`、`auth{deviceToken, role, scopes}`、`policy{maxPayload, ...}`。

**challenge 握手（服务端）：** `src/gateway/server/ws-connection.ts` L349-354 —— 连接建立即发：
```ts
const connectNonce = randomUUID();
if (connectionKind === "gateway") {
  send({ type: "event", event: "connect.challenge", payload: { nonce: connectNonce, ts: Date.now() } });
}
```
首帧门禁：`message-handler.ts`（见 §1）。

**challenge 签名（客户端）：** `packages/gateway-client/src/device-auth.ts`
```ts
export function buildDeviceAuthPayload(params): string {
  return ["v2", params.deviceId, params.clientId, params.clientMode, params.role,
          scopes.join(","), String(params.signedAtMs), token ?? "", params.nonce].join("|");
}
```
客户端 `protocol-client.ts` `handleMessage` 里拦截 `connect.challenge`，取出 `nonce`/`ts` 后 `sendConnect(socket, generation)`，`buildConnectPlan({nonce, challengeTs})` 组装签名后的 connect 参数。

**服务端验签：** `src/gateway/server/ws-connection/connect-device-proof.ts` —— `verifyGatewayConnectDeviceProof()`：
- `derivedId = deriveDeviceIdFromPublicKey(device.publicKey)`，必须等于 `device.id`；
- `Math.abs(Date.now() - signedAt) > DEVICE_SIGNATURE_SKEW_MS (2min)` → 拒绝；
- `device.nonce !== context.handler.connectNonce` → 拒绝；
- `resolveDeviceSignaturePayloadVersion()`（`handshake-auth-helpers.ts` L297-340）用 v3/v2 两种 payload 分别 `verifyDeviceSignature(publicKey, payload, signature)`，全失败 → 拒绝。

**seq 生成（服务端）：** `src/gateway/server-broadcast.ts`
```ts
const clientSeq = new WeakMap<GatewayWsClient, number>();
...
const nextSeq = (clientSeq.get(c) ?? 0) + 1;
frame = frameWithSequence(base, nextSeq, payloadFragment);   // {"type":"event","event":...,"seq":N,...}
clientSeq.set(c, nextSeq);
```
关键注释（L490）：`// Consume the seq for the dropped frame so the client's gap detector sees the loss instead of a silently thinner stream.`（dropIfSlow 时仍 `clientSeq.set(c, nextSeq)`）。L596-599：`// Targeted frames ride the same per-client sequence as fanout frames...`。

**stateVersion（服务端）：** `packages/gateway-protocol/src/schema/snapshot.ts`
```ts
/** Monotonic version counters for snapshot subtrees. */
export const StateVersionSchema = closedObject({ presence: Type.Integer(), health: Type.Integer() });
```
`src/gateway/server/health-state.ts`：模块级 `let presenceVersion = 1; let healthVersion = 1;`，`incrementPresenceVersion()` / health 刷新时 `healthVersion += 1`；`buildGatewaySnapshot()` 把 `stateVersion: { presence: presenceVersion, health: healthVersion }` 放进 snapshot。广播时 `opts.stateVersion` 可序列化进事件帧（`server-broadcast.ts` `serializeFrameField("stateVersion", opts.stateVersion)`）。

**客户端 seq 检测 + 事件不重放：** `packages/gateway-client/src/protocol-client.ts`
```ts
private lastSeq: number | null = null;
private connect(): void {
  ...
  this.lastSeq = null; // Outer event sequences belong to one WebSocket generation.
  ...
}
```
```ts
const seq = typeof parsed.seq === "number" ? parsed.seq : null;
if (seq !== null) {
  if (this.lastSeq !== null && seq > this.lastSeq + 1) {
    const expected = this.lastSeq + 1;
    this.invoke("gap", () => this.opts.onGap?.({ expected, received: seq }));
    ...
  }
  this.lastSeq = seq;
}
```
`ui/src/app/gateway-store.ts`（Control UI 消费端）：`onGap: ({ expected, received }) => { ...setSnapshot(...); if (isCurrentClient(nextClient)) connect(); }` —— **检测到缺口就整体重连**，重连的 hello-ok 带全量 snapshot，完成"刷新状态"。

**重连恢复：** `protocol-client.ts` `handleClose()` → `opts.resolveClose(context)`（客户端策略）→ 若 `decision.retry` 则 `scheduleReconnect(decision.reconnectDelayMs ?? connectFailure?.reconnectDelayMs, retryAfterMs)`；`RetrySupervisor` 初始 1s、×2、上限 30s（`client.ts` 的 `reconnect: { initialMs: 1_000, multiplier: 2, maxMs: 30_000 }`），服务器可通过 `retryable + retryAfterMs` 施加更长的退避。`generation` 每次 `connect()` 递增，旧 socket 的迟到帧被 `isActive(socket, generation)` 丢弃（防止重连竞态混帧）。

### 对 Astrion 的借鉴意义
- 协议要显式区分"握手期"与"认证后"，并强制"首帧 connect"；challenge→签名→hello 三步式认证能同时防重放（nonce）和防中间人（签名绑定 deviceId+clientId+role+scopes+token+nonce）。
- "事件不重放 + seq 间隙检测 + 断线重连全量 snapshot" 是**无状态客户端投影**范式的核心，Astrion 的 gateway 可以完全照搬：服务端只做 `seq = per-connection counter`，客户端重连后向 `snapshot` 类方法全量拉取，避免服务端维护事件缓存与游标。
- `stateVersion` 用"子树单调计数器"（presence/health）而非全局版本号，客户端可精确知道哪个子树过期。Astrion 可扩展为 `{sessions, agents, runs, presence}` 多组计数器。

---

## 3. Session 状态归属

### 结论
真状态（sessions/transcript/runs）**全部归 gateway 进程所有**：默认存文件（每 agent 一个 `sessions.json`），也支持 SQLite（`session.store` 配置，transcript 存 `transcript_events` 表）；共享状态（设备身份、配对、token）存 `state/openclaw.sqlite`。多客户端只是"投影"：通过 `sessions.list/describe/catalog` 读取、`sessions.subscribe / sessions.messages.subscribe` 订阅、`sessions.changed / session.message` 事件增量更新。存在 CAS/etag 机制：session entry 的 `lifecycleRevision`（randomUUID，每次变更轮换）+ 写操作的 `expectedLifecycleRevision`/`expectedSessionId`（乐观锁）；transcript 层还有 `expectedLeafEntryId` 分支叶 CAS。

### 关键文件与代码证据

**存储实现：**
- `src/config/sessions/paths.ts`：`resolveDefaultSessionStorePath(agentId) = <stateDir>/agents/<id>/sessions/sessions.json`；`resolveSessionStorePathForScope()`（`session-store-path.ts`）支持 `storePath` 覆盖与 `sqlite:` 前缀。
- `src/config/sessions/session-accessor.sqlite-transcript-store.ts`：SQLite transcript 实现，`createTranscriptEventInserter()` 插入 `transcript_events` 表（含 `seq`、`eventJson`、`createdAt`）；配套 `session-transcript-index.fs.ts`（文件索引，`seq` 由 index+1 生成）。
- 共享 SQLite：`src/state/openclaw-state-db.paths.ts` —— `<stateDir>/state/openclaw.sqlite`，存设备身份/配对/token（见 §5）。

**会话行与 lifecycleRevision（etag）：**
- `src/config/sessions/session-accessor.sqlite-entry-store.ts` L442：`previousEntry.lifecycleRevision === normalizedEntry.lifecycleRevision`（写前比对/冲突检测）。
- `session-accessor.sqlite-message-cut.ts` L300：`currentEntry.lifecycleRevision !== params.expectedState.lifecycleRevision` → 拒绝；L577：`lifecycleRevision: params.forked ? randomUUID() : params.currentEntry.lifecycleRevision`。
- `session-accessor.sqlite-transcript-write-guard.ts` L24：`entry.lifecycleRevision === scope.expectedLifecycleRevision && ...` —— transcript 写入围栏。

**CAS 参数（协议侧）：** `packages/gateway-protocol/src/schema/sessions-patch.ts`
```ts
export const SessionsPatchParamsSchema = closedObject({
  key: NonEmptyString, agentId: Type.Optional(NonEmptyString),
  /** Reject the mutation if the session was reset or replaced before it commits. */
  expectedSessionId: Type.Optional(NonEmptyString),
  expectedLifecycleRevision: Type.Optional(NonEmptyString),
  ...
});
```
`logs-chat.ts` `ChatSendParamsSchema` 里 `expectedLeafEntryId`（`Transcript-branch CAS ... the client's displayed branch leaf`）、`expectedSessionRoutingContract` 等。

**多客户端投影：**
- 读取：`src/gateway/server-methods/sessions-read.ts` L196 `"sessions.list"`（含 store_load / materialization / sharing / active_run_flags 等阶段，并把结果按客户端权限过滤）。
- 订阅：`sessions.subscribe`、`sessions.messages.subscribe/unsubscribe`（`core-descriptors.ts` 方法表：`sessions.subscribe`, `sessions.messages.subscribe`, `sessions.messages.unsubscribe`, `sessions.viewers.set`）。
- 变更广播：`src/gateway/server-methods/session-change-event.ts` —— `context.broadcastToConnIds("sessions.changed", eventPayload, connIds, ...)`，按 sessionKey/agentId 计算受众（`resolvePrivateSessionEventBroadcastScope`），只发给订阅了该 session 的连接。
- 会话事件带 transcript 消息级 seq：`src/gateway/server-session-events.ts`（`messageSeq = stored.seq`）→ `session-transcript-message.ts` 把 `params.messageSeq` 投射为事件 `seq`。
- 订阅注册表：`src/gateway/server-chat-state.ts`（`SessionMessageSubscriberRegistry`），广播端用 `sessionMessageSubscribers.get(sessionKey)` 判断该连接是否订阅后才推 `session.message` 等（`server-broadcast.ts` `requiresSessionSubscription` 分支）。

### 对 Astrion 的借鉴意义
- 采用"Gateway 单所有者 + 客户端投影"模型：客户端拿到的只是 hello snapshot + 增量事件拼接的镜像，写操作一律走 RPC 并在 gateway 内做冲突检测——Astrion 应把 session/run 状态收敛进 gateway 进程，控制面完全不落盘。
- `lifecycleRevision + expectedLifecycleRevision` 的乐观锁范式非常轻量；Astrion 可在 Python 侧实现为每个 session 一个 `revision: UUID`，所有 patch/send 带 `expected_revision`。
- 事件广播的"受众过滤"（按 sessionKey + 订阅者集合）值得借鉴，避免把私密 transcript 广播给未订阅连接。

---

## 4. 幂等与去重

### 结论
副作用方法（sessions.create、sessions.send、channels.send、node.invoke 等）在参数 schema 层强制 `idempotencyKey`；服务端实现分两类：
1. `sessions.create`：**内存级去重缓存**（按 principal/device 分组的 Map，TTL 5 分钟，容量上限），同时去重**并发 inflight**（同 key 共享同一个 Promise）并校验"同 key 同参数"（params 的 sha256）。
2. 消息类（chat send / source reply / channel 消息）：幂等键由 `runId + 投递指纹 + 操作 id` 组合，去重靠**扫描已持久化 transcript 中同 idempotencyKey 的 message** + FIFO 租约互斥。

### 关键文件与代码证据

**sessions.create（内存去重缓存）：** `src/gateway/server-methods/session-create-idempotency.ts`
```ts
const sessionCreatesByContext = new WeakMap<GatewayRequestContext, Map<string, Map<string, SessionCreateEntry>>>();
// owner = principal ? `principal:${principal}` : `device:${deviceId}`
// requestIdentity = sha256(stableStringify(request.params))
const existing = entries?.get(idempotencyKey);
if (existing) {
  if (existing.requestIdentity !== requestIdentity) → INVALID_REQUEST "idempotency key was reused with different parameters"
  ... scope/authorization 变化也拒绝
  const result = existing.state.kind === "completed" ? existing.state.result : await existing.state.work;  // inflight 去重
  request.respond(result.ok, ..., { ...result.meta, cached: true });
  return;
}
// 容量：DEDUPE_MAX=1000（server-constants.ts），超限返回 UNAVAILABLE
```
TTL：`packages/gateway-protocol/src/schema/sessions-create.ts` —— `export const SESSION_CREATE_IDEMPOTENCY_RETENTION_MS = 5 * 60_000;`（仅 ok 结果保留缓存，失败即释放）。

**消息类幂等键构造：** `src/agents/tools/message-tool-idempotency.ts`
```ts
export function buildMessageToolDeliveryFingerprint(params) {
  const canonical = JSON.stringify(canonicalizeMessageToolIdempotencyValue({ action, params: stripEnvelope(params.params) }));
  return sha256Base64UrlPrefix(canonical, 24);
}
export function buildMessageToolAutogeneratedIdempotencyKey({ runId, deliveryFingerprint, operationId }) {
  return `${runId}:message-tool:${deliveryFingerprint}:${operationId}`;
}
```

**消息去重（持久化扫描 + 租约）：** `src/gateway/internal-source-reply-persistence.ts`
```ts
const lease = leaseKey ? internalSourceReplyPersistenceLeases.reserve([leaseKey]) : undefined;  // FIFO 租约,并发互斥
await lease?.wait();
// 先扫描：completePersistedInternalSourceReply() → findTranscriptEvent(scope, event =>
//   message?.idempotencyKey === params.idempotencyKey && isOpenClawDeliveryMirrorAssistantMessage(message))
```
transcript 写入层支持显式去重模式：`session-accessor.sqlite-transcript-store.ts` 中 `idempotencyKeyMode?: "dedupe" | "preserve-owner" | "relocate-owner"`，以及 `idempotencyLookup: "scan"`。

**node.invoke（节点侧队列去重）：** `src/gateway/node-runtime-state.ts` L83 `const existing = queue.find((entry) => entry.idempotencyKey === params.idempotencyKey);`——执行节点侧用内存队列按 key 去重。

### 对 Astrion 的借鉴意义
- 协议 schema 层把 `idempotencyKey` 设为必填（非空字符串），客户端重试时必须复用同一 key，这是网关化系统在网络抖动下不产生重复副作用的根基。
- "内存 Map + TTL + 容量上限 + inflight 共享 Promise" 是极简且正确的服务端去重缓存实现；Astrion 在 Python 可用 `dict + asyncio.Future` 复刻，注意同样要校验"同 key 同参数"（hash 参数）与授权元数据不变。
- 消息类侧采用"幂等键进 transcript + 落盘扫描"的持久化去重，比纯内存缓存更抗网关重启；Astrion 若把 transcript 落盘，可把 idempotencyKey 作为消息的唯一索引来做幂等。

---

## 5. 设备配对与认证

### 结论
- 每次 gateway WS 连接建立后，服务器先发 `connect.challenge`（一次性 nonce + ts）；客户端用设备 Ed25519 私钥对 `v2/v3` 拼接载荷签名后放进 connect 的 `device{id, publicKey, signature, signedAt, nonce}`。
- 服务端验签：`device.id` 必须由 publicKey 派生、签名时间在 2 分钟窗口内、nonce 必须等于该连接下发的 nonce、Ed25519 签名必须通过。
- 配对流程：`device.pair.requested/resolved` 事件 + `device.pair.approve/reject` 方法（`PAIRING_SCOPE` 权限）；配对记录（pending/paired）与 bootstrap token 都持久化在共享 SQLite（`state/openclaw.sqlite`）。
- 设备 token：连接成功（授权）后 `ensureDeviceToken` 颁发/轮换，hello-ok 返回 `deviceToken`；支持 `device.token.rotate/revoke` 主动吊销，吊销会 invalidate 已连接客户端（close 4001）。
- bootstrap token 是短时效一次性凭据（首次配对用），兑换后才有完整 profile。

### 关键文件与代码证据

**challenge 下发：** `src/gateway/server/ws-connection.ts` L349-354（见 §2）。

**验签：** `src/gateway/server/ws-connection/connect-device-proof.ts`（见 §2）。

**配对存储：** `src/infra/device-pairing-store.ts` —— SQLite 表 `device_pairing_paired` / `device_pairing_pending` / `device_bootstrap_tokens`（`openOpenClawStateDatabase`，`state/openclaw-state-db.js`）；`DevicePairingStoreState = { pendingById, pairedByDeviceId }`，写事务持锁（`withDevicePairingLock`）。
- 路径：`state/openclaw-state-db.paths.ts` —— `resolveOpenClawStateSqlitePath() = <stateDir>/state/openclaw.sqlite`。

**配对请求/审批：** `src/infra/device-pairing.ts`（`requestDevicePairing`，事件 `device.pair.requested`），`src/infra/device-pairing-approval.ts`（`approveDevicePairing`，支持 auto-approve、并发协调、scope 基线校验）；`src/gateway/server/ws-connection/connect-device-pairing.ts`（`authorizeGatewayConnectDevice`，把 pending 转 paired 并下发 scopes）。

**token 颁发/轮换/吊销：** `src/infra/device-pairing-tokens.ts`
```ts
export type RotateDeviceTokenDenyReason = "unknown-device-or-role" | "missing-approved-scope-baseline"
  | "scope-outside-approved-baseline" | "caller-missing-scope";
// ensureDeviceToken / rotateDeviceToken / revokeDeviceToken 均在配对锁内读写 paired record 的 tokens
```
token 生成：`src/infra/pairing-token.ts` —— `randomBytes(32).toString("base64url")`，`verifyPairingToken` 用常数时间比较（`safeEqualSecret`）。

**bootstrap token：** `src/infra/device-bootstrap.ts` —— `generatePairingToken()` 颁发（TTL `DEVICE_BOOTSTRAP_TOKEN_TTL_MS`），一次性兑换 `redeemDeviceBootstrapTokenProfile`；兑换入口在 `sendGatewayHello`（`connect-hello.ts`）的 `authMethod === "bootstrap-token"` 分支。

**吊销联动：** `src/gateway/server/ws-connection/authenticated-request-dispatch.ts`
```ts
const DEVICE_CREDENTIAL_INVALIDATING_METHODS = new Set([
  "device.pair.remove", "device.token.rotate", "device.token.revoke", "node.pair.remove",
]);
```
这些方法成功后，携带旧凭据的连接被 `invalidateGatewayPolicyClient(...close(4001, "client invalidated: ..."))` 踢下线。

**设备身份（gateway 自身）：** `src/infra/device-identity.ts` —— Ed25519 身份存共享 SQLite（`device_identity` 表），`deriveDeviceIdFromPublicKey`、`signEd25519Payload`、`verifyEd25519Signature`（`ed25519-signature.ts`）。

### 对 Astrion 的借鉴意义
- "服务器先发 challenge 再收 connect" 的次序很关键：nonce 必须由服务器生成，客户端签名后回传，才能防重放。
- "deviceId 由公钥派生"（无注册中心也能识别同一物理设备）+"配对审批+token 轮换/吊销+踢线" 是完整闭环：Astrion 可先在本地做"首台设备自动 approved"（`autoApproveNewDeviceScopes`），再扩展审批流。
- token 落 SQLite + 常数时间比较是低成本高安全性的做法，Python 端（secrets.compare_digest）同样适用。

---

## 6. 协议 codegen（TypeBox → JSON Schema → Swift）

### 结论
TypeBox schema 本身就是 JSON Schema 兼容对象；每 feature 一个 `protocol-schema-fragment-*.ts`，合成唯一注册表 `ProtocolSchemas`；运行时校验用 TypeBox/compile 惰性编译出 validator；**Swift 模型由脚本直接吃 TypeBox schema 对象（当作 JSON Schema 遍历）生成** `GatewayModels.swift`，CI 用 `--check` 比对防止漂移。`closedObject` 用 `additionalProperties:false` 强化封闭性，并打隐藏 symbol 标记以保留名义身份。

### 关键文件与代码证据

**TypeBox 定义：** `packages/gateway-protocol/src/schema/frames.ts` / `snapshot.ts` / 各 feature fragment；原始类型在 `primitives.ts`（`NonEmptyString = Type.String({minLength:1})`）；封闭对象工厂 `closed-object.ts`：
```ts
export function closedObject<Properties extends TProperties>(properties: Properties) {
  const schema = Type.Object(properties, { additionalProperties: false });
  Object.defineProperty(schema, identityKey, { value: Symbol("closedObject") });  // 隐藏 symbol，不入 JSON
  return schema;
}
```

**注册表合成：** `schema/protocol-schemas.ts` —— `ProtocolSchemas = composeProtocolSchemaFragments([...16 个 fragment])`；`schema/protocol-schema-composer.ts` 遍历合成并**拒绝重复 key**。

**JSON Schema → 运行时 validator：** `protocol-validator.ts`
```ts
import { Compile, type Validator as TypeBoxValidator } from "typebox/compile";
compiled ??= Compile(schema as never);   // 惰性编译
```
`validator-registry.ts` 给每个协议类型导出 `validateXxx = compile(S.XxxSchema)`，服务端/客户端共用。

**Swift 生成链路：** `scripts/protocol-gen-swift.ts`
- 导入 `ProtocolSchemas` 与 `ErrorCodes`（`packages/gateway-protocol/src/schema/error-codes.js`）；
- 把每个 TypeBox/JSON Schema 对象经 `stableJson()` / `schemaSignature()` 归一化后**直接当作 JSON Schema 遍历**（读 `properties/required/items/enum/anyOf/oneOf/patternProperties` 等），生成 Swift struct/class；
- 输出写到 `apps/shared/OpenClawKit/Sources/OpenClawProtocol/GatewayModels.swift`；header 注明 "Generated by scripts/protocol-gen-swift.ts — do not edit by hand"；
- `--check` 模式用于 CI 校验产物与当前 schema 一致；配套 `scripts/format-swift.sh`。
- Schema 对象在 JS 侧还用于 `GatewayFrameSchema` 判别联合（`discriminator: "type"`），供 quicktype/codegen 产出更紧的类型。

**协议版本：** `packages/gateway-protocol/src/version.ts` —— `PROTOCOL_VERSION = 4`, `MIN_CLIENT_PROTOCOL_VERSION = 4`, `MIN_NODE_PROTOCOL_VERSION = 3`；connect 请求带 `minProtocol/maxProtocol` 区间，hello-ok 回显服务器当前版本（`connect-hello.ts`）。

### 对 Astrion 的借鉴意义
- "单一 schema 源 →（服务端校验器 / 客户端模型 / 文档）" 的单一事实源值得照搬。Astrion 若用 Python，可选择 Pydantic v2（`model_json_schema()` 直接产 JSON Schema）+ JS 客户端侧校验或生成 TS 类型；关键是**禁止手工再写一遍协议类型**。
- "封闭对象（additionalProperties:false）"强制了前向兼容纪律：客户端发未知字段直接被拒绝，服务器加字段必须走 optional + 版本协商。
- 一对协议版本区间（min/max）+ hello-ok 回显版本，让新旧客户端/节点共存变得可管理，Astrion 的 gateway 协议应从一开始就带版本协商。

---

## 7. 方法与事件目录

### 结论
方法以"名称.类别"扁平命名，核心方法在 `CORE_GATEWAY_METHOD_SPECS` 一张策略表里集中声明（名称、family、scope、since 版本、是否 control-plane write / advertise / startup）；插件方法由插件注册表附加。事件由 `GATEWAY_EVENTS` 清单声明，广播时按 `EVENT_SCOPE_GUARDS` 做 scope 过滤，部分事件要求 session 订阅。

### 关键文件

**方法：** `src/gateway/methods/core-descriptors.ts`（`CORE_GATEWAY_METHOD_SPECS`，核心方法约 400+ 个），`src/gateway/methods/registry.ts`（注册表），`src/gateway/server-methods/core-handlers.ts`（handlers 挂载）。

**事件：** `src/gateway/server-methods-list.ts`（`GATEWAY_EVENTS`），`src/gateway/server-broadcast.ts`（`EVENT_SCOPE_GUARDS` + `SESSION_SUBSCRIPTION_EVENTS`）。

### 方法类别（按 family 聚合统计）
- **会话与运行**（最大族）：`sessions.*` 57+（list/get/describe/create/send/abort/patch/reset/delete/compact/recover/fork/rewind/subscribe/viewers/goal/groups/branches/usage/diff/files/compaction…）；`runs` 相关融入 send/abort/subscribe。
- **节点/worker**：`node.*` 19（pair.list/approve/reject/remove、list/describe、invoke、pending.pull/ack/enqueue/drain、runnerInventory.update、pluginTools/skills.update、event…）。
- **设备**：`device.*` 9（pair.list/approve/reject/remove/rename、token.rotate/revoke、scopes.requestUpgrade/waitUpgrade）。
- **agent/模型/技能**：`agents.*`（list/create/update/delete/files/workspace）、`models.*`、`skills.*`（36 项，含建议接受）。
- **配置与运维**：`config.get/set/apply/patch/schema`、`health`、`status`、`diagnostics.*`、`doctor.*`、`logs.tail`、`update.*`、`migrations.*`、`gateway.*`（重启/suspend）、`cron.*`（10）、`channels.*`（7）、`secrets.*`、`worktrees.*`、`projects.*`、`desktop.*`。
- **人机交互/审批**：`exec.approval.*` / `exec.approvals.*`（11）、`question.*`（5）、`plugin.approval.*`、`openclaw.approval.*`、`talk.*`（16，语音）。
- **系统 agent 引导**：`openclaw.chat/history/setup.*/changes.list`、`wizard.*`。
- **工具/媒体/周边**：`tools.*`（7）、`terminal.*`（7）、`board.*`（9）、`canvas.*`、`portal.*`、`progressCard.*`、`tts.*`（9）、`push.*`（7）、`users.*`（20）、`voicewake.*`、`mentions.*`、`messages.*`、`assistant.*`、`attach.*`、`artifacts.*`、`audit.*`、`mcp.*`（7）、`conversations.*`（4）等。

### 事件类别（GATEWAY_EVENTS，约 90 个）
- 握手：`connect.challenge`。
- 会话/聊天：`sessions.changed`、`chat`、`chat.metadata.changed`、`ui.command`、`session.message/observer/operation/sharing/typing/tool/suggestion/approval`。
- 存在与健康：`presence`、`health`、`heartbeat`、`tick`、`shutdown`、`gateway.suspension`。
- 节点/设备：`node.pair.requested/resolved`、`node.presence`、`node.hostStats`、`node.invoke.*`（cancel/input/request）、`device.pair.*`（changed/requested/resolved/setup.completed/deliveryUncertain）。
- 审批/提问：`exec.approval.requested/resolved`、`question.requested/resolved`、`plugin.approval.*`、`openclaw.approval.*`。
- 系统：`cron`、`task`、`task.suggestion`、`update.available`、`update run changed`、`config.changed`、`skills.changed`、`users.prefs.changed`、`mentions.changed`、`voicewake.*`、`talk.mode/event`、`terminal.data/exit`、`portal.changed`、`progressCard.changed`、`controlUi.sessionPullRequests.changed`、`plugins.controlUi.changed`、`sessions.catalog.host`。

### 对 Astrion 的借鉴意义
- "一张集中策略表（方法名 + scope + since + 属性）+ 注册表查重 + hello-ok 下发 methods/events 白名单" 让能力发现（feature discovery）成为协议一等公民。Astrion 可在 hello 响应里下发 `features.methods/events`，客户端据此决定 UI 能力。
- 事件统一走 `EVENT_SCOPE_GUARDS` 过滤矩阵（event → 需要的 scope 列表），比在 handler 里各自检查权限更不易漏；Python 侧可做一个装饰器 `@event("session.message", requires="read")` 并集中注册。

---

## openclaw 设计要点速查表（≤10 行）

1. **单 Gateway 长驻**：HTTP+WS(noServer) 统一 upgrade 路由；每连接一个 handler（认证门禁/keepalive/慢消费/预算），连接注册表 = `Set<GatewayWsClient>`。
2. **协议**：WS 文本 JSON；首帧必须 `connect`；`{type:req,id,method,params}` → `{type:res,id,ok,payload|error}`；事件 `{type:event,event,payload,seq?,stateVersion?}`；协议版本 = 4（min/max 协商）。
3. **认证**：连接先收 `connect.challenge{nonce,ts}`，客户端用设备 Ed25519 私钥签名 `v2|deviceId|clientId|mode|role|scopes|signedAt|token|nonce` 回传；服务端验 id 派生、2min 时间窗、nonce、签名。
4. **事件不重放**：seq 为每连接单调计数（被丢弃的帧也消耗 seq）；客户端 `seq > lastSeq+1` → `onGap` → 整体重连；重连 hello-ok 带**全量 snapshot**，`stateVersion{presence,health}` 定子树版本。
5. **会话状态归 Gateway**：sessions.json（每 agent）或 SQLite transcript（`transcript_events` 带 seq）；共享状态在 `state/openclaw.sqlite`；客户端只投影（sessions.list/subscribe + sessions.changed 事件）。
6. **CAS/乐观锁**：session `lifecycleRevision=UUID` 每次变更轮换；写操作带 `expectedLifecycleRevision/expectedSessionId/expectedLeafEntryId`。
7. **幂等**：副作用方法必填 `idempotencyKey`；sessions.create 用内存 Map（TTL 5min、容量 1000、inflight 同 key 共享 Promise、sha256 参数校验）；消息类用"RunId:指纹:opId"键 + transcript 扫描去重 + FIFO 租约。
8. **配对/租约吊销**：bootstrap token（32B base64url、短时效一次性）→ 配对审批（pending/paired 表）→ `ensureDeviceToken` 轮换；rotate/revoke/pair.remove 使旧连接 close(4001)。
9. **Schema 单一源**：TypeBox schema（closedObject=additionalProperties:false）→ 惰性编译 validator + `scripts/protocol-gen-swift.ts` 直读 schema 生成 `GatewayModels.swift`（CI --check 防漂移）。
10. **方法/事件目录**：核心方法集中在 `CORE_GATEWAY_METHOD_SPECS`（name/family/scope/since）一张表；事件在 `GATEWAY_EVENTS` + `EVENT_SCOPE_GUARDS` 过滤矩阵；hello-ok 下发 `features.methods/events` 供客户端能力发现。