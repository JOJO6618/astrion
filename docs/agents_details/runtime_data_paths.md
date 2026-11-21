# 运行态数据目录与路径变量（完整）

> 本文由 `AGENTS.md` §1.5 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 1.5) 运行态数据目录与路径变量（2026-06 更新）

> 设计目标：运行态数据（对话、用户工作区、日志等）默认存放在用户主目录的 `~/.astrion/astrion/` 下，对齐 `~/.claude`、`~/.codex` 惯例，**不落在源码树内**。配置实现见 `config/paths.py`。

### 1.5.1 默认位置与模式分流

`ASTRION_DATA_ROOT` 可整体搬迁数据根。Host 数据和日志分别沿用 `<data_root>/host/data/` 与 `<data_root>/host/logs/`。`settings.json` 和部署级 `config/` 继续位于数据根内。`web/` 下的旧租户数据留给旧版部署。主仓在路径初始化前拒绝 `TERMINAL_SANDBOX_MODE=web`、`docker` 或旧 `LINUX_SAFETY`。

### 1.5.2 路径解析优先级（从高到低）

1. **具体目录环境变量**：`DATA_DIR` / `LOGS_DIR`（单独覆盖某个目录，最高优先级）
2. **数据根目录环境变量**：`ASTRION_DATA_ROOT`（整体搬迁运行态数据根目录，默认 `~/.astrion/astrion`）
3. **兜底默认**：`~/.astrion/astrion/host`

具体目录变量支持相对路径（相对仓库根目录展开）、绝对路径与 `~`。

> 注意：`config/*.json` 分两类：
> - **程序能力**（`docker_risk_markers.json`、`skill_hints.json`、`command_blocking_recommended.json`）：是程序行为的一部分，随版本演进，仍锚定源码树。`prompts/`、`agentskills/` 同理。指令推荐规则仅供用户主动导入，禁止作为执行时的默认禁令或回退。
> - **部署级配置**（`custom_models`、`host_workspaces`、`auto_approval`、`goal_review`、`host_sandbox_policy`）：因部署/机器而异或含密钥，外置到 `~/.astrion/astrion/config/`（即 `DEPLOY_CONFIG_DIR`，可单独用该环境变量覆盖，host/web 共享）。读取走 `config.resolve_deploy_config(name)`，回退链：部署目录 → 源码树 `.json` → 源码树 `.json.example`，因此开发环境不必先跑 setup 也能用源码树种子。
>
> **个人指令拦截（2026-10）**：唯一权威为 `modules/command_blocking.py`。个人文件 `command_blocking.json` 与解析共享链接后的 `personalization.json` 同目录，格式 `{enabled: true, rules: []}`；用户间隔离、同用户跨工作区共享，host 免登录沿用其 `DATA_DIR`。普通用户可调用 `/api/command-blocking` GET/POST（full/headless 共用），设置→通用的开关与路径授权旁的规则窗口共享状态。保存立即影响后续前台/后台命令和终端输入；部分更新不能覆盖未提交字段。沿用不区分大小写的全文包含匹配，空列表不回退，关闭保留规则。旧部署级 `forbidden_commands.json` 和硬编码词表均不再使用，也不自动迁入个人配置。

### 1.5.3 旧租户数据归属

Host 登录和 CLI 使用可信 Host 工作区目录。旧 Web 普通账号、API 用户及其数据保留在原版本的数据根。跨版本导入与部署切换见 [拆分方案](../docker_fork_split_plan.md)。

### 1.5.4 日志策略（2026-06）

- API 请求体落盘（dump）默认**关闭**，由 `AGENT_API_DUMP_ENABLED` 控制（`1/true/yes/on` 开启）。
- 日志混合轮转（实现见 `utils/log_rotation.py`）：
  - 追加型单文件（`host_workspace_debug.log`、`chunk_*.log`、`conn_diag.log`、`api_debug.log`、TaskLogger/ErrorLogger）按大小轮转，默认单文件 20MB、保留 3 份。
  - 按份计量目录（`api_requests/` 一请求一文件）只保留最近 N 个，默认 30。
  - 阈值可覆盖：`AGENT_LOG_ROTATE_MAX_BYTES` / `AGENT_LOG_ROTATE_BACKUPS` / `AGENT_DUMP_KEEP`。

### 1.5.5 数据迁移

- 迁移脚本：`scripts/migrate_runtime_data.py`（源码树 → 运行态根；复制+备份+可回滚+幂等，`logs/` 丢弃不迁）。
  - 脚本 `import config` 复用程序同一套路径解析（含 `.env`），**模式由 `.env` 决定**；执行前务必先 `--dry-run` 确认目标。
- 清理误迁副本：`scripts/cleanup_misplaced_web.sh`。

### 思考摘要显示文件（2026-10-29）

`modules/thinking_summary/storage.py` 以真实 manager 的 `_get_conversation_file_path` 解析所在目录，独立写入 `<实际对话目录>/thinking_summaries/<conversation_id>.json`，沿用 Agent／Studio namespace、工作区及 Chat 账号隔离。每次发布原子保存最新摘要、版本、代次和该思考块摘要序列，不写原对话文件或模型上下文；读取缺文件不创建目录。完整生成与显示契约见 `docs/thinking_summaries.md`，本次仅语法验证。

### 私有项目常驻记忆（2026-11-07）

新增 `modules/workspace_memory.py` 统一解析源码树外的 MEMORY.md。Host 使用当前数据目录的 `memory/<目录名>-<规范化项目路径摘要>/MEMORY.md`。主子只在新建和压缩时全文加载，不在恢复／普通请求中刷新；具体冻结元数据、授权与迁移边界见 `docs/workspace_memory.md`。
