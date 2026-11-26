# Web 资产与独立预览交接

## 当前范围

本轮实际修改集中在 `backend-rust/src/preview/` 与 `test/2026-11-26_Rust预览/`。收到独立预览迁移指令后，工作转到预览路由和兼容验证。此前分配的版本化、浅备份前像和 Web 静态资产未由本轮修改或复验。工作树中的 `backend-rust/src/versioning/`、`backend-rust/src/runtime/versioning.rs`、`backend-rust/src/web_assets.rs` 与 `test/2026-11-24_Rust后端/assets_contract.rs` 是后续核对入口。Responses writer 与审批回调的兼容状态仍待从相邻负责人处确认。

## 已有实现

- `backend-rust/src/preview/mod.rs` 管理独立监听器、预览令牌和对话作用域。独立监听器首次需要时绑定 `127.0.0.1:0`。复用符合格式的旧 `metadata.preview_token`，空闲会话缺令牌时按旧字段写回，运行中或写入失败时用进程内派生令牌。独立请求重新验证注册工作区与对话归属。
- `backend-rust/src/preview/routes.rs` 提供 Web `targets`、`targets/remove`、`file`、`proxy`，以及独立源的 token file 和 token proxy。代理方法包括 GET、HEAD、POST、PUT、DELETE、PATCH，并返回 OPTIONS。每次代理请求重新读取当前对话已登记端口。移除目标按运行时存储锁更新旧 metadata，且会向该对话的活动任务发布 `preview_targets_updated`。文件读取复用现有工作区文件能力。
- `backend-rust/src/preview/transport.rs` 把网络目标固定在 `127.0.0.1` 的已登记端口，继承原查询并限制响应体为 50 MiB。实现 HTML `<base>` 和常见根相对资源属性改写，不自动跟随重定向。转发时剥离 Cookie、Authorization、CSRF 以及连接级请求头。远端同源回退附加 `sandbox allow-scripts allow-forms`，赋予预览文档不透明源。代理还移除上游 Set-Cookie 和 CORS 响应头。这些回退、重定向与请求头处理较 Python 旧实现更严格，兼容影响尚无浏览器实测。
- 主智能体完成了 `runtime.rs`、`lib.rs`、`host_web.rs`、`runtime/web_messages.rs` 和 `web_status/mod.rs` 共享接线。Web 预览路由并入已有浏览器会话与工作区中间件，同一个 `Preview` 实例供 targets、bootstrap 和 messages 下发 `preview_base` 与 `preview_token`。独立监听器由模块惰性启动。

## 已观察到的证据

- 主智能体通知 `backend-rust/target-master/debug/astrion-headless` 已重建。用该二进制执行 `test/2026-11-26_Rust预览/preview_web.test.mjs`，夹具在私有 HOME、工作区和数据目录内启动正常 Web 入口，使用合成 Python 格式对话与本机回环上游。
- 实测断言顺序确认了未登录的 targets、bootstrap、messages 与同源 file 返回 401，正常 Web 登录成功。三个已登录投影下发相同的预览基址、旧令牌和目标列表，基址端口独立于主监听器。独立文件请求返回 HTML 和正确的 `<base>`，PNG 的 MIME 与字节匹配，无效令牌返回 404。
- HTTP 用例停在 `preview_web.test.mjs:156`，通过 `fetch` 设置 `Host: forged.invalid` 后收到 200，断言预期 403。尚未确认请求实际发出的 Host，也尚不能据此判断是 Node `fetch` 的行为还是监听器守门失效。该用例在断言之后的分支均未执行。
- Rust 契约 `test/2026-11-26_Rust预览/preview_contract.rs` 已写入。此前运行 `cargo test --lib runtime::preview::tests` 时，工作树里仍缺 Chat handoff service 与 Git 工作区契约测试文件，测试未执行。后续的新二进制验证不等于 Rust 契约已经通过。

## 待验

- 用能明确发出自定义 Host 的本机 HTTP 客户端复核伪造 Host、缺失端口和错误端口，保留有效 `127.0.0.1:<port>` 与 `localhost:<port>` 作为对照。复跑完整 HTTP 夹具，确认独立源不挂主 API、跨对话端口拒绝、PATCH 转发及敏感头剥离、远端回退、CSRF 拒绝与目标删除后即时撤销。
- 复跑 Rust 契约，核对 URL 编码与查询原样转发、各方法、HTML 和重定向改写、越界与私密文件过滤、工作区注销后旧令牌失效，以及 Python 令牌写回和重启后复用。
- 独立预览做了既有 metadata 的读写兼容；Rust 对新写 HTML 文件或模型输出的自动预览目标捕获和事件入口尚无本轮实现或验证。无显式对话 ID 的部分 Web 请求使用最近一次该工作区的预览投影，多个窗口同工作区时的当前对话归属、工作树预览和远端回退子资源仍待实测。旧 Python 同源代理与目标删除的用户级限流未在本模块移植。

交接时按指令暂停开发。未修改前端、未提交，写本交接文档期间未运行测试。
