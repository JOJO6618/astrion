# 项目结构（完整）

> 本文由 `AGENTS.md` §1 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 1) 当前项目结构（按代码现状）

- **后端入口**
  - `main.py`: 统一启动入口（当前默认走 Web 模式 + thinking_mode=True）
  - `server/app.py`: 推荐的 Web 服务入口（封装并转发到 `server/app_legacy.py`）
  - `web_server.py`: 兼容入口，已标记 deprecated，但仍可启动
- **后端核心目录**
  - `server/`: Flask 业务主线（chat/task/status/context 已拆分为子包：`server/chat/`、`server/status/`、`server/tasks/`、`server/context/`（用户资源/身份/个性化，原 `server/context.py` 已拆包并保留兼容 re-export）；**REST 任务轮询为唯一实时通道**——Socket.IO 已于 2026-09-11 整体移除，详见 §12.5）
  - `server/context/`（2026-10-12）：`workspace.py` 解析可信身份／工作区路径；`data.py` 与 `data_decorators.py` 提供不装配执行资源的存储访问；`resources.py` 仅创建／复用运行实例；`background.py` 聚合后台状态并合并异步激活；`modes.py`、`status_view.py`、`sub_agent_views.py` 提供对应读视图。原兼容 re-export 保留。
  - `server/runtime/`: 公共任务入口（2026-09 Gateway 化阶段二新增）：`context.py` 定义 RuntimeContext 三层模型（TrustedPrincipal/TaskParams/InternalDirectives），`service.py` 提供 RuntimeService（create_task/cancel/guidance/queue/get_task_events）；契约见 `docs/runtime_contract.md`
  - `core/`: 终端与工具编排（`main_terminal.py`、`web_terminal.py`、`main_terminal_parts/*`；其中 `main_terminal_parts/context/` 和 `main_terminal_parts/tools_definition` 已拆分为 base + mixin 子包）
  - `modules/`: 可复用能力模块（terminal/file/memory/sub_agent/upload_security/user 等；`file_manager`、`persistent_terminal`、`terminal_ops`、`mcp_client_manager` 已拆分为子包）；`composer_drafts.py` 负责原子草稿与保存水位，`conversation_modes.py` 共用生效模式规则，`skills_revision.py`／`skills_sync.py` 负责工作区技能版本与准备。`utils/conversation_manager/preparation.py` 合并执行侧存储准备。
  - `config/`: 配置拆分（`api.py`, `limits.py`, `terminal.py`, `paths.py` ...），由 `config/__init__.py` 聚合并加载 `.env`
  - `utils/`: API client、日志、上下文与对话工具等公共函数（`api_client`、`tool_result_formatter`、`context_manager`、`conversation_manager` 已拆分为子包；原入口文件保留为兼容入口）
- **前端目录**
  - `static/src/`: Vue 3 + TS 前端；`app/methods/ui/workspaceNavigation.ts` 统一导航，`workspaceSelection.ts` 排序选择写入，`workspaceStartup.ts` 解析初始真实身份，`composerDrafts.ts` 管理草稿作用域，`navigationRefresh.ts` 负责一次辅助刷新。`workspaceRequestScope.ts` 为同源 API 捕获派发身份，`stores/workspaceConversationCache.ts` 按工作区／类型／筛选缓存列表。
  - `cli/src/`: opentui（Zig 内核 + Yoga）+ React 19 CLI 前端（2026-09-11 起正式替代 Ink 版；旧 Ink 实现存档于 `cli/legacy-ink-src/` 仅作字段参考）
  - 监控动画相关核心文件：
    - `static/src/components/chat/monitor/MonitorDirector.ts`
    - `static/src/stores/monitor.ts`
    - `static/src/components/chat/monitor/*`
- **CLI 相关文档**（本地文档，未随仓库发布）
  - `docs/cli_ui_display_spec.md`: CLI 显示层设计说明
  - `docs/cli_slash_commands_spec.md`: CLI `/` 指令设计说明
- **其他子项目/资源**
  - `android-webview-app/`: Android WebView 客户端工程
  - `modules/sub_agent/`: 子智能体执行逻辑（主进程内 `asyncio.Task`，工具调用复用主进程链路）
  - `easyagent/`: 旧版 Node.js 子智能体实现，暂时保留但已不再使用
  - `_experiments/`: 本地实验残留与历史文档归档目录（**不纳入 git**，见 §7）
