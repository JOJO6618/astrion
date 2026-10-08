# CLI 交互与分层（完整）

> 本文由 `AGENTS.md` 的 §2（CLI 命令）、§3（CLI 最小验证）、§4（CLI 分层映射）、§5（CLI 当前交互约束）四处的 CLI 相关内容**原样汇总**而来（2026-10-08 压缩 AGENTS.md 时导出；其中若干条原文在压缩中丢失，已按 2026-08/09 的原文补回，见文末「补回记录」）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，此处放 CLI 的完整命令、分层职责与交互行为。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## A) CLI 运行环境与命令（原 §2）

- **运行环境**：bun ≥1.3（opentui FFI；node ≥26.4 亦可，本机 node v24 不可用）
- 开发启动：`bun cli/src/main.tsx`（或 `npm --prefix cli run dev`）
- 类型检查：`cd cli && ./node_modules/.bin/tsc --noEmit`
- 可执行命令名：`astrion`（已装到 `/opt/homebrew/bin/astrion` → `cli/bin/astrion` symlink，bun wrapper 支持 symlink 解析；`--version`/`--help` 快速退出不启 TUI；后续 `bun build --compile` 单文件分发）
- 依赖安装：沙箱网络受限时不跑 npm/bun install，从 `cli-redesign-demo/node_modules` 铺平（版本以 `cli/package.json` 为准）
- 前端框架：`cli/src/` 为 opentui（Zig 内核 + Yoga）+ React 19（2026-09-11 起正式替代 Ink 版；旧 Ink 实现存档于 `cli/legacy-ink-src/`，仅作字段参考）

## B) CLI 最小可复现验证（原 §3）

- `cd cli && ./node_modules/.bin/tsc --noEmit`
- 无头冒烟：`testRender` 抓帧脚本（写完即用即删，参考 cli-redesign-demo 的验证模式；gateway 指向 `127.0.0.1:9` 不碰真实服务）

## C) CLI 改动优先级：分层职责映射（原 §4）

`cli/src/` 分层——新交互先定归属层再动手：

| 层 | 文件 |
|------|------|
| 入口编排 | `main.tsx` / `boot.tsx` |
| 主界面 | `app.tsx` |
| Gateway 客户端（Bearer） | `gateway.ts` |
| 会话运行时（发消息 / 事件轮询 / 审批弹出） | `runtime.ts` |
| 菜单状态机 | `menuState.ts` |
| 渲染分发 | `menu.tsx` |
| 通用件 | `components.tsx` / `selector.tsx` |
| 数据层 | `data.ts` |
| 文案 | `i18n/` |

## D) CLI 当前交互约束（2026-09-11 重写，原 §5）

- **CLI 全部走 Gateway host Bearer 通道**（规范来源：`docs/runtime_protocol.md` §6，现归档为 `_experiments/docs_archive/docs/runtime_protocol.md`）：token 读 `~/.astrion/astrion/host/data/host_api_token`，请求带 `Authorization: Bearer` 请求头；**禁止** Web 会话通道（Cookie/CSRF/host-login）。工作区经 `X-Astrion-Workspace-Id` 头绑定（`server/gateway_auth.py` 解析）。
- **启动指令 `astrion`**：cwd = 工作区，运行内不可切换；cwd 未注册为工作区时先提示并询问创建（确认面板 **Esc = 取消退出**）。
- **服务自启动**：CLI 不与 Web 端启动绑定——探测无服务时自动 spawn `python3 -m server.headless_app --path <cwd> --port <port> --thinking-mode`（detached + unref，显式端口）。**服务在运行但无 host token = 旧版本服务，提示重启**（token 由服务端启动时生成，在 `initialize_system` 的 host 分支）；请求 401 自动重读 token 重试一次（自愈）。**边界：CLI 只在探测无服务时启动新实例，绝不 kill/重启已在运行的进程。**
  - ⚠️ 2026-10-08 按代码校正：原文写的是 `python3 -m server.app --port 8091 --thinking-mode`，**已过时**。真实 spawn 为 `cli/src/gateway.ts:149` 的 `['-m', 'server.headless_app', '--path', cwd, '--port', String(port), '--thinking-mode']`（headless 入口见 §2）。
- **审批面板自动弹出**：收到新 ID 的 `tool_approval_required` 事件即弹出（不依赖手动 `/approvals`）；**键盘 ←→ 仅选择「本次允许/拒绝」，Enter 提交人工决定**。**同 ID 更新保留用户已收起的决定**（不重新弹开）；单次完全访问在开启自动审核时须两项均通过；`decision` 接口成功**不等于**工具已经执行；面板等 `tool_approval_resolved` 终态后清理。
- **多语言读 OS 语言设置**（`cli/src/i18n/`，`LC_ALL`/`LANG` 检测 zh/en，启动时定死，不跟随 Web 个人空间）；CLI 内所有文案**禁止按字符长度硬编码布局**——**选中反色只落文字节点**，**项间固定间距**。
- `Enter` = 发送、`Shift+Enter` = 换行。
- 不要在未获用户要求时运行交互式 TUI 压测或长时间模拟输入（刷屏占上下文）。

## E) 补回记录（2026-10-08）

以下条目在 2026-10-08 的 AGENTS.md 压缩中从本文档集合里丢失，本次按压缩前原文补回本文件：

| 补回内容 | 来源 |
|---------|------|
| CLI 分层 → 具体文件映射（前 C 节整表） | 压缩前 §4 |
| Gateway 规范来源 `docs/runtime_protocol.md` §6 | 压缩前 §5 CLI 首条 |
| 请求头字面量 `Authorization: Bearer` | 同上 |
| 无 host token = 旧版本服务、提示重启（`initialize_system` host 分支） | 压缩前 §5「服务自启动」 |
| 未注册 cwd 创建确认面板 **Esc = 取消退出** | 压缩前 §5「启动指令」 |
| 审批面板 **←→ 本次允许/拒绝、Enter 提交** | 压缩前 §5「审批面板自动弹出」 |
| **同 ID 更新保留用户收起决定** | 同上 |
| `cli/src/i18n/`、`LC_ALL`/`LANG` 检测 zh/en | 压缩前 §5「多语言」 |
| **选中反色只落文字节点、项间固定间距** | 同上 |
| token 完整路径 `~/.astrion/astrion/host/data/host_api_token` | 压缩前 §5 CLI 首条 |
