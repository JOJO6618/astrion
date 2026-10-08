# 代码索引 codegraph（完整）

> 本文由 `AGENTS.md` §17 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 17) 代码索引（codegraph，2026-10-06 新增）

> **状态**：本项目开发已开始试用 `codegraph`（`colbymchenry/codegraph`）作为代码结构索引，**推荐日常开发使用**。
> **定位**：精确的**代码结构查询器**（tree-sitter 符号图 + SQLite/FTS5），**不是语义搜索引擎**——它按词法匹配符号名与代码词，不做 embedding 语义检索。

### 17.1 安装与建索引

- 全局 CLI：`/opt/homebrew/bin/codegraph`（npm 全局包 `@colbymchenry/codegraph`；实测版本 v1.6.2，自带运行时与原生内核，无需编译）
- 在项目根建索引：

```bash
codegraph init --yes .
```

- 索引产物 `.codegraph/` **已加入 `.gitignore`**（机器特定，不入库）
- **遥测已关闭**（`codegraph telemetry off`，状态存 `~/.codegraph/telemetry.json`）；**换机器或重装后需重新关闭**——它默认开启
- 实测：**946 文件 / 18,711 节点 / 49,134 边 / 2.4 秒 / 索引库 59MB**
- 语言覆盖：Python 423、TypeScript 241、Vue 135、JavaScript 85、Rust 22、TSX 20、Kotlin 7；识别出 264 个 `route` 节点与 135 个 `component` 节点
- 自动遵守项目 `.gitignore`，自动跳过 `node_modules`/`dist`/`build`/`target`/`.venv`/`Pods`，无需额外配置

### 17.2 常用命令

| 命令 | 用途 |
|------|------|
| `codegraph explore "<查询>"` | **首选**：一次返回相关符号源码 + 调用路径 + 影响面 + 关系分类 |
| `codegraph impact <symbol> --depth 2` | 改动影响面，**含 HTTP 路由反向关联**（如 `POST /api/xxx → handler`） |
| `codegraph callers <symbol>` | 谁调用了它 |
| `codegraph callees <symbol>` | 它调用了谁 |
| `codegraph query <name>` | 按符号名搜索，返回定义位置与 import 点 |
| `codegraph node <symbol>` | 单符号源码 + 调用者/被调用链 |
| `codegraph affected [files...]` | 改动会影响哪些测试文件（支持 `git diff --name-only \| codegraph affected --stdin`） |
| `codegraph files` | 索引内的文件结构 |
| `codegraph status` | 索引统计 |
| `codegraph sync` | 手动增量同步（**CLI 无 watcher，大改动后需跑**） |

### 17.3 使用要点（实测踩坑，务必遵守）

1. **查询必须用符号名或英文术语**：`explore` 是**词法匹配而非语义匹配**（官方原话 "matches symbol/file names and indexed code words lexically, not by meaning"）。**纯中文自然语言查询会直接失败**（返回 "No relevant code found"）；中文查询必须夹带英文标识符——`"消息池 pending_master_messages 派发"` ✅ / `"子智能体输出如何通过消息池派发给主智能体"` ❌。
2. **改代码前先查影响面**：`codegraph impact <symbol>` 一次给出受影响符号与关联路由，比 grep 逐层追溯高效（实测 0.17 秒返回 25 个受影响符号）。
3. **行号可信，但 `callers` 建议交叉验证**：实测多次抽查，返回行号与磁盘内容逐字一致；查不存在的符号会明确报 not found（不编造）。但 `callers` 列表可能不完整——实测有调用点只出现在 `impact` 中而缺失于 `callers`。
4. **CLI 无文件 watcher**：索引是静态快照，改代码后需 `codegraph sync`；自动同步仅在 MCP 模式（`codegraph serve --mcp`）下由 watcher 提供。
5. **MCP 与 CLI 是两条独立通道**：MCP 工具（默认只暴露 `codegraph_explore` 一个）仅主 agent 可见；**子智能体看不到 MCP 工具，需经 Bash 调 CLI**。本项目当前**未接入 MCP**。
6. **Astrion 不在 `codegraph install` 的自动配置列表内**（该命令只支持 Claude Code、Cursor、Codex CLI、opencode、Hermes Agent、Gemini CLI、Antigravity IDE、Kiro、GitHub Copilot）；如需接入需手动配置 stdio server。
7. **检索结果有噪音**：`explore` 会带出 FTS 命中的不相关符号，需人工过滤。

### 17.4 与项目记忆联动

使用过程中如发现 codegraph 的新优点或新问题，记录到项目记忆 `codegraph_code_index.md`。
