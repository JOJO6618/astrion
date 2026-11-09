# 组件阅览室 · 保存与合并规则回归（2026-10-31）

针对 `design-library/room/reviewRules.ts`、`design-library/room/useReviews.ts`、`design-library/tooling/review-server.ts`
的独立功能回归。**只读源码，不修改任何 design-library 文件**；所有临时 fixture 和构建产物都落在本目录内。

## 运行

```bash
cd test/2026-10-31_组件阅览室
node build.mjs                                                        # esbuild 打包 TS -> .build/*.mjs
node --test rules.test.mjs server.test.mjs useReviews.test.mjs        # 全量
# 单文件
node --test rules.test.mjs
node --test server.test.mjs
node --test useReviews.test.mjs
```

- 依赖：Node ≥ 20（实测 v24.11.1）、项目已有的 `node_modules/esbuild`（0.21.5）、`node_modules/vue`（useReviews 用）。**不安装任何依赖**，不需要浏览器。
- `build.mjs` 用 `packages: 'external'` 打包，`vue` / `node:*` 全部外置；产物在 `.build/`，可随时删除重建。

## 目录

| 文件 | 内容 |
| --- | --- |
| `build.mjs` | 用 esbuild 把 `reviewRules.ts` / `review-server.ts` / `useReviews.ts` 打成临时 ESM |
| `harness.mjs` | 临时 fixture 根目录（`.tmp/`）、reviewServer 中间件调用通道、请求工具 |
| `rules.test.mjs` | `validateDecisions` / `finalMergeTarget` 规则 |
| `server.test.mjs` | `/__review` 接口：GET / PUT / 409 / 403 / 400 / 405 / 413 / 损坏文件 |
| `useReviews.test.mjs` | mock `fetch` + `localStorage` 驱动真实 `useReviews` |

`design-library/.local/reviews.json` **从不被读写**：每个用例都在 `.tmp/root-xxxx/` 下重建自己的
`candidates/` 与 `.local/`，用例结束即删除。

## 请求通道（重要）

`server.test.mjs` 默认启动真实 `http.createServer` 监听 `127.0.0.1:0`，用 `fetch` 访问，即真实的
临时 localhost 接口。

**但当前子智能体沙箱禁止任何 `listen`**（TCP / UDP / Unix socket 全部返回 `EPERM`），因此在本环境运行时
会自动降级为 `inproc` 通道：

- 仍然调用**真实的 `reviewServer()` 中间件函数**（不是复制实现）；
- 请求侧是**真实的 `http.IncomingMessage`**（真实 headers 大小写归一、真实 `for await` 读流）；
- 响应侧捕获 `writeHead` / `end`，按状态码 / 响应头 / JSON 断言；
- 每个用例断言完全相同，测试代码不区分通道；
- `node --test` 输出末尾的「（诊断）本次请求通道」用例会打印实际使用的通道。

降级通道**不经过 TCP 协议栈**，因此不覆盖「端口绑定 / 连接层」这类问题；在允许监听的机器上重新运行同一条命令
即可走真实 localhost 路径，无需改代码。

> **验证状态（2026-10-31）**
> - **主侧（主智能体环境）已用真实 TCP 通道复核全量 50/50**：`.test-result.log` 打印
>   `[transport] 使用真实 127.0.0.1 TCP 服务`，结尾 `pass 50 / fail 0`，无取消或跳过。
> - **子侧（编写本测试的子智能体沙箱）使用 `inproc` 通道 50/50 通过**：该沙箱对 TCP / UDP / Unix socket 的 `listen`
>   均返回 `EPERM`；该通道不经过 TCP 协议栈。
> - 全量包含首次 GET 失败后的 2 项恢复回归：flush 必须重新 GET，不能假报 saved 或覆盖远端新记录。
> - `.test-result.log` 保存主侧最终 50 项真实 TCP 运行输出，已加入 `.gitignore`，不属于测试源码。

## 覆盖范围与结果（50/50 通过，主侧真实 TCP 与子侧 inproc 分别验证）

### rules.test.mjs — 15 项
- 空决策 / `pending` / `keep` 通过
- **同类合并允许**（单跳）
- **跨类合并拒绝**（`请选择同类别的组件`）
- **自合并拒绝**（`不能合并到自身`）
- **循环链拒绝**：双向环、三节点环（正序与逆序）、`btn-a→btn-b→btn-c→btn-b` 链外环
- **正常链允许**：`A→B→C`、链尾无决策、链尾 `keep`
- 未知候选、无效状态（含缺 `status`）、非合并状态带 `targetId`、合并目标不存在
- 备注边界：2000 字通过 / 2001 字拒绝 / 非字符串拒绝 / 缺 `note` 拒绝
- `finalMergeTarget`：单跳、多跳、链尾、无决策、循环

### server.test.mjs — 21 项
- **GET 空记录**：`{schemaVersion:1, revision:0, updatedAt:null, decisions:{}}`，且不创建 `.local`
- **PUT 写文件**：revision 0→1、`updatedAt` 可解析、磁盘内容一致、文件权限 `0600`、临时文件已清理
- **重新 GET**：返回与 PUT 完全一致的记录；连续两次 PUT revision 0→1→2
- **revision 409**：回传磁盘当前 `document`，且文件字节不变
- 非法状态 400 / 坏 JSON 400 / 2001 字备注 400（2000 字通过）/ payload 结构非法（数组、缺 revision、字符串 revision、负 revision、decisions 非对象）400——全部不落盘
- **跨域写入拒绝**：`http://evil.example` 403、同 IP 不同端口 403、缺 `Origin` 403；GET 不受限
- 缺/错 `X-Component-Room`、`Content-Type: text/plain` → 403
- 非本机 `Host`（`evil.example`、`localhost.evil.example`）→ 403
- `POST` / `DELETE` → 405；>512KB body → 413（真实 TCP 下可能表现为连接重置），不落盘
- 未知候选 / 跨类别 / 自合并 / 循环在写入前 400；正常链 `A→B→C` 可写入并持久化
- 写入只保留 `status/targetId/note`，多余字段被丢弃
- **损坏磁盘文件不覆盖**：坏 JSON、`schemaVersion` 不符、负 `revision`、磁盘里存在跨类别决策 → GET/PUT 均 500，文件字节原样保留
- 非 `/__review` 路径 `next()`

### useReviews.test.mjs — 14 项（mock fetch + localStorage，无浏览器）
- 初始化后 `ready` / `saved` / 远端记录
- 300ms 内三次编辑合并为一次 PUT（去抖 + 写入排队）
- 保存中继续编辑 → 同一轮 flush 补发第二次 PUT，且用新 revision
- 草稿恢复（`baseRevision` 一致）→ 自动回写并清 dirty；`baseRevision` 落后 → `conflict` 且不自动写盘
- 非法草稿（未知候选）被丢弃，改用磁盘记录
- 409 → `conflict`，冲突期间不再写盘；`useRemote()` 采用远端；`useDraft()` 用远端 revision 重试成功
- 规则拦截（自合并、跨类别）返回 false、本地状态不变、不落盘；切出 `merge` 自动清 `targetId`
- GET 失败但有草稿 → 恢复草稿并标记 `error`；`ready` 前 `update` 被拒；`reviewedCount` 只统计非 `pending`
- **GET 持续失败时 flush 重试**：只会重新 `initialize()`，状态停在 `error`、`revision` 不前进、一次 PUT 都不发，
  绝不假报 `saved`（含显式再 `flush()` 一次）
- **首次 GET 失败 → 离线编辑 → 磁盘已被改成 revision 2**：flush 重新 GET 后进入 `conflict`（不发 revision 0 的 PUT），
  本地草稿内容保留；`useDraft()` 用远端 revision 2 提交成功（落到 revision 3）

## 发现的源码问题（未改源码，仅报告）

1. **磁盘记录校验过严导致无法在 UI 里自救（确定）**：`reviewServer.read()` 对磁盘文件做 `schemaVersion` / `revision` /
   `validateDecisions` 全量校验，任何一条历史决策失效（例如候选被删除或改名后 `候选不存在：x`）都会让 GET 和 PUT
   同时返 500；而 PUT 在写之前也要 `read()`，所以也无法通过接口修正。用户只能手工编辑 `reviews.json` 或删文件。
   用例「磁盘文件里存在违规决策（跨类别）」证实了这一行为。
2. **错误文案复用（确定，属于提示缺陷）**：`validateDecisions` 对“`note` 缺失/非字符串”和“`note` 超 2000 字”都返回
   `备注最多2000字`；对非 merge 决策“缺少 `targetId` 字段”返回 `仅合并状态可以指定目标`。手工编辑或旧格式文件缺字段时
   文案会误导排查。
3. **超大请求体的响应可能不是 413（推测，置信度中）**：中间件读到 >512KB 就 `send(413)` 并 return，不再消费请求流；
   真实 TCP 下客户端可能先拿到 `ECONNRESET`，前端会显示网络错误而不是“审阅记录过大”。进程内通道无法证实，需 TCP 环境复核。
4. **每次 GET/PUT 都全量读盘校验**：`readCatalog()` 会遍历所有 `candidates/*/*/source.json` 并解析（性能提示，非缺陷）。
5. `useReviews` 目前的初始化/窄化逻辑在本次回归中行为正常（草稿恢复、冲突、排队均符合预期）。

`reviewRules.ts` 未发现逻辑错误：同类合并、跨类拒绝、自合并拒绝、循环（含链外环）、正常链均符合预期，
并用变异测试验证断言有效（删掉自合并检查 → 对应用例失败；删掉 Origin 检查 → 跨站用例失败）。

## 已知限制

- 未做视觉验证、未启动浏览器；`useReviews.exportFile()`（依赖 `Blob` / `window.document`）不在覆盖范围。
- 真实 TCP 本地接口已由主侧验证（`.test-result.log`）；子侧沙箱仍只能跑进程内通道，两边结论一致但通道不同。
- 变异测试（人为改坏源码副本）只用于自检断言有效性，不构成对源码的改动。
- 测试只针对上述三个文件的当前版本；`useReviews.ts` 若继续修改（主智能体正在修复初始化引用与状态窄化），需要重跑
  `node build.mjs && node --test ...`。
