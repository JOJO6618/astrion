# docs/ 目录约定

> 建立于 2026-10-08（AGENTS.md 压缩整理时）。

## 这个目录是干什么的

`docs/` **是 git 追踪目录**（`.gitignore` 中已移除 `docs/`），里面只放两类东西：

1. **从 `AGENTS.md` 拆出来的细节文档** —— `AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单、完整命令放这里。
2. **开发文档** —— 后续新增的设计稿、契约、方案、排查记录等。

不放的东西：历史物料堆（结课论文、旧 prompt 备份、第三方 clone 等）。

## 与 AGENTS.md 的关系

- `AGENTS.md` 的「文档地图」表列出每个主题对应的细节文档与「改什么前必读」的触发条件。
- **主节号（§0–§22）与 `AGENTS.md` 保持一致**（细节文档头部注明了来源小节）。项目记忆与既有文档按号引用 `AGENTS.md`，所以编号不要重排。
- **但 17 个编号子标题只存在于细节文档**（§1.5.1–1.5.5、§13.1–13.3、§14.1–14.3、§15.1–15.2、§17.1–17.4 等）；按 `§13.2` 这类子号查找时请到对应细节文档。

## 目录索引

### `agents_details/` —— AGENTS.md 拆出的细节文档

| 文件 | 对应 AGENTS.md 小节 | 内容 |
|------|-------------------|------|
| `project_structure.md` | §1 | 完整目录职责、监控动画核心文件 |
| `runtime_data_paths.md` | §1.5 | 路径解析、host/web 双路径、日志轮转、数据迁移、个人指令拦截 |
| `commands.md` | §2 | 各形态启动说明、headless 额外路由、Python 解释器探测、桌面壳打包链路 |
| `testing.md` | §3 | 具体冒烟用例、CLI 最小验证、pytest/unittest 执行细节 |
| `frontend_design_style.md` | §5.5 | 前端设计规范 11 条细则与配色示例 |
| `android_release.md` | §8 | Android 发布联动完整步骤 |
| `permission_and_sandbox.md` | §10 / §10.7 / §10.8 | 权限模式四档、执行环境、路径授权语义、审核智能体配置、各平台沙箱实现细节与实测记录 |
| `work_mode.md` | §10.6 | 运行模式三档语义与完整实现清单 |
| `multi_agent.md` | §11 | 多智能体前端类型模型、派发链路、消息池硬约束、formatter/renderer 清单、调试约定 |
| `main_task_gate.md` | §12 | 门闸本体、token 移交链路、Socket.IO 移除后的替代机制对照 |
| `tool_loading.md` | §13 | 工具动态加载完整约束与 checklist |
| `web_extract_and_search.md` | §14 | 提取链与降级链、提取商契约、新增搜索服务商 checklist |
| `settings_and_providers.md` | §15 | 设置页分区清单、提供商 catalog 字段与协议裁决 |
| `preview_panel.md` | §16 | 预览服务器安全模型、docker 分流与代理架构、隔离细节 |
| `codegraph.md` | §17 | 代码索引安装、命令与实测踩坑 |
| `sub_agent_access.md` | §18 | 子智能体固定权限、单次完全访问、审批面板 UI 细则 |
| `display_snapshot.md` | §19 | 运行中对话显示快照契约与禁止旁路清单 |
| `linux_host_sandbox.md` | §20 | Linux 沙箱助手、身份核验、文件根、网络回收与验证证据 |
| `host_password.md` | §21 | Host 网页密码存储、认证、桌面豁免与回归 |
| `api_costs.md` | §22 | 主/子费用账本、价格捕获、币种与等待态 |
| `cli_workflow.md` | §2 / §3 / §4 / §5（CLI 部分） | CLI 分层职责映射、运行命令、最小验证、交互约束（含压缩时丢失条目的补回记录） |

> **注意**：`cli_workflow.md` 是 2026-10-08 人工汇总新建的（不是原样下沉），用于补回 CLI 相关在压缩中丢失的条目。
>
> 没有对应细节文档的小节（§0 项目名、§4 代码修改约定、§5 风格与质量、§5.6–§5.8 三类强制规范、§6 Git 工作流、§7 安全与仓库卫生、§9 给 Agent 的硬性要求）本身是「规则」性质，全文在 `AGENTS.md`。⚠️ 但这**不等于内容与压缩前原文完全一致**——这些小节在压缩中是「要点化重写」而非逐字搬运，存在若干条目级丢失（2026-10-08 审核后已按原文补回主要项）；需要逐字原文时以压缩前的 git 历史版本为准。

### 开发契约文档（由 `AGENTS.md` 直接引用）

这几份不是从 `AGENTS.md` 拆出来的，而是**本来独立存在的开发契约文档**。因为 `AGENTS.md` 里有直接引用，为保证引用可达，从归档区保留回本目录：

| 文件 | 内容 | 被谁引用 |
|------|------|---------|
| `runtime_contract.md` | RuntimeContext 三层模型 + RuntimeService 契约 | `AGENTS.md` §1 |
| `providers_api.md` | 提供商后端 API 契约 | `AGENTS.md` §15 |
| `dynamic_tool_loading_plan.md` | 工具动态加载设计文档 | `AGENTS.md` §13 |
| `运行对话显示快照契约.md` | 运行中对话显示快照契约 | `AGENTS.md` §19 |
| `conversation_api_costs.md` | 主子智能体费用账本契约 | `AGENTS.md` §22 |
| `crosspet_integration.md` | 可选本机桌宠状态协议及生命周期 | `AGENTS.md` §24 |

### 其他开发文档

| 文件 | 内容 | 说明 |
|------|------|------|
| `coauthor_trailer.md` | Commit 共同作者尾注完整规范（格式、邮箱、多作者、自检） | 2026-10-08 新建，`AGENTS.md` §6 引用；原先该规范只存在于本地私有项目记忆，clone 后读不到 |

## 已归档的旧文档去哪了

原 `docs/` 中未被引用的 26 个文件、以及原 `doc/` 全部 410 个文件（含 `anthropic-skills/` 第三方仓库）已于 2026-10-08 移到**未追踪**目录：

```
_experiments/docs_archive/docs/   # 原 docs/ 内容（运行时契约、设计稿、排查记录等）
_experiments/docs_archive/doc/    # 原 doc/ 内容（历史物料、旧设计稿、prompt 备份等）
```

`_experiments/` 已在 `.gitignore` 中，所以这些文档不进版本控制、也不会公开。

> 注意：`AGENTS.md` 与部分代码注释里仍有指向 `doc/xxx.md` 的引用（如 i18n 规范）。这些引用**保留原路径写法**，实际文件需加 `_experiments/docs_archive/` 前缀才能找到；指向 `docs/` 下已归档文件的旧引用同理。
