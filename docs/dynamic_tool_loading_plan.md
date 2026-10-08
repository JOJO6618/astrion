# 工具动态加载（Deferred Tool Loading）实施方案

> 状态：设计已定稿（2026-09-10 讨论结论），待实施。
> 关联：`docs/runtime_contract.md`（状态责任 S1、默认值解析优先级）、`core/tool_config.py`、`prompts/` 冻结机制。

## 1. 背景与目标

当前每个 API 请求都在顶层 `tools` 字段全量携带 ~40 个工具定义（源码约 108KB，估算 15~25K tokens/请求），其中工作流（7 个）、对话回顾、个性化设置等工具在多数对话中从未使用。工具定义膨胀不仅费 token，还会降低工具选择准确率（Anthropic 公开数据：相似命名工具是选错工具的头号原因）。

**目标**：低频工具默认不进入 `tools` 数组，仅在 system prompt 中保留「类目 + 使用时机 + 工具名」目录；模型需要时通过常驻的 `load_tools` 工具按需取回完整 JSON 定义并直接调用。

**核心收益**（默认配置下）：
- 每请求工具定义开销预计减少 40%~60%（延迟 19 个工具，目录仅 250~400 token）
- 工具候选变少，选择准确率提升
- 机制通用，不依赖 kimi-k3 私有的 system 消息注入特性，全模型可用

## 2. 已定稿的关键决策（讨论结论固化）

| # | 决策 | 结论 |
|---|------|------|
| D1 | 注入方式 | 通用设计：`load_tools` 在 **tool result** 中返回完整 JSON 定义，模型随后直接调用；不采用 kimi-k3 原生 system+tools 消息注入 |
| D2 | 状态权威 | **对话文件 metadata 为准**，个人空间设置只在创建对话时快照一次；之后改设置不影响已有对话（对齐 runtime_contract「对话元数据绑定 > 用户偏好快照」） |
| D3 | 功能开关 | 个人空间可开/关，**默认开启**；老对话（无字段）与 `enabled=false` 的对话不生效（全量工具，现状行为） |
| D4 | prompt 载体 | 独立文件 `prompts/tool_loading.txt`，经**现有冻结机制**（`_get_or_init_frozen_prompt`，新增 key `frozen_tool_loading_prompt`）冻结在对话 metadata |
| D5 | prompt 目录 | 创建即冻结、终身不变；目录列完整延迟集，不随 loaded 状态变化（避免 system prompt 变动破缓存） |
| D6 | 压缩重置 | 对话压缩（深度+浅度）后：`loaded=[]`、`pending=deferred_set`、`initial_exposed` 不变；冻结 prompt 不动 |
| D7 | 缓存设计 | tools 数组对话终身不变（initial_exposed 钉死）；加载走 tool result 纯追加；前缀缓存零破坏 |
| D8 | close_sub_agent | 彻底删除（僵尸工具：定义存活、执行分支已在拆分重构中丢失），原位置留注释防误恢复 |
| D9 | 工具分层 | 常驻含 recall/search_project_memory、vlm_analyze、read_skill、view_image/video 等 ~22 个；延迟注册表 19 个（含 create_sub_agent 等 3 个子智能体工具、trigger_easter_egg） |
| D10 | 加载工具命名 | `load_tools`（按名加载；搜索语义由 prompt 目录承担，不做关键词模糊搜） |
| D11 | 架构约束 | 全部改动走新 gateway 架构：状态归 conversation metadata（S1 单写者），执行链路不读 personalization / Flask session |
| D12 | v1 边界 | 多智能体对话不启用；MCP 工具不纳入；不做对话级覆盖 UI；不做前端"已加载工具"可视化 |

## 3. 工具分层清单

### 3.1 常驻基础层（钦定，不可配置，~22 个）

```
read_file, write_file, edit_file, run_command,
terminal_session, terminal_input, terminal_snapshot, sleep,
todo_create, todo_update_task, ask_user,
web_search, extract_webpage, save_webpage,
view_image, view_video,           # 按模型能力条件注入（现状逻辑不变）
submit_plan, read_skill,
recall_project_memory, search_project_memory,
vlm_analyze,                      # 仅非多模态模型（现状逻辑不变）
load_tools                        # 新增；仅当对话启用动态加载且延迟集非空时注入
```

### 3.2 可延迟注册表（19 个，用户可逐个配置默认注入/延迟）

| 类目 key | 类目标签 | 工具 | 目录文案（"什么时候用"） |
|---|---|---|---|
| workflow | 工作流 | activate_workflow, report_workflow_stage, choose_workflow_branch, get_workflow_status, deactivate_workflow, list_workflows, save_workflow | 按既定流程执行任务、创建/调整工作流、推进或查询已激活的工作流时 |
| sub_agent | 子智能体 | create_sub_agent, get_sub_agent_status, terminate_sub_agent | 并行处理独立任务、批量或后台执行、查询/终止子智能体时 |
| conversation | 对话回顾 | conversation_search, conversation_review | 查找或回顾本工作区的历史对话时 |
| memory_write | 记忆写入 | update_memory, update_project_memory | 记录用户偏好、项目约定或重要决策时 |
| personalization | 个性化 | manage_personalization | 修改称呼、语气、主题等个性化配置时 |
| skill_create | 技能 | create_skill | 把经验沉淀为可复用 skill 时 |
| mcp | MCP | list_mcp_servers | 查看或刷新 MCP 服务与工具映射时 |
| misc | 彩蛋 | trigger_easter_egg | 触发隐藏彩蛋时 |

**推荐默认延迟集 = 上表全部 19 个**（即开箱即省）。

### 3.3 其他层（现状不变）

- `disabled_tools`（管理员禁用）：优先级最高，被禁工具不进目录、不可被加载
- 自定义工具 / MCP `mcp__*` 工具：不入延迟体系，维持现状动态追加
- 多智能体主工具集（`build_master_tools_for_conversation`）：v1 不涉及

## 4. 数据结构

### 4.1 对话 metadata（`metadata.tool_loading`，S1 管辖）

```json
{
  "tool_loading": {
    "enabled": true,
    "deferred_set": ["activate_workflow", "..."],
    "initial_exposed": ["read_file", "...", "load_tools"],
    "loaded": [],
    "pending": ["activate_workflow", "..."]
  }
}
```

| 字段 | 写入时机 | 之后是否可变 |
|---|---|---|
| `enabled` | 创建时（= 当时个人空间总开关） | 不可变 |
| `deferred_set` | 创建时（= 用户配置的延迟工具 ∩ 注册表 ∩ 未禁用） | 不可变（压缩重置的锚点） |
| `initial_exposed` | 创建时（= 常驻层 + 未延迟工具 + 条件性 load_tools） | 不可变 |
| `loaded` | `load_tools` 成功后追加；压缩后清空 | 运行时可变 |
| `pending` | 创建时 = `deferred_set`；此后 = `deferred_set − loaded` | 运行时可变（与 loaded 同一写入点重算） |

冻结 prompt 文本存 `metadata.frozen_tool_loading_prompt`（字符串，走现有 `_get_or_init_frozen_prompt` 懒冻结），与结构字典分离：define_tools/守门只读结构字典，prompt 构建只读冻结文本。

### 4.2 个人空间配置（personalization.json）

```json
{
  "tool_loading_enabled": true,
  "tool_loading_deferred": ["activate_workflow", "... 19 个默认全选 ..."]
}
```

- `tool_loading_enabled`：bool，默认 `true`
- `tool_loading_deferred`：string[]，默认 = 注册表全部 19 个；只影响**新建**对话

## 5. 改动清单（按模块）

### Phase 0：close_sub_agent 清理（独立 commit）

| 文件 | 改动 |
|---|---|
| `core/main_terminal_parts/tools_definition/agent_tools.py` | 删除工具定义，原位置留注释：该工具自 2025-11-15 起即 terminate_sub_agent 别名，main_terminal 拆分时执行分支丢失，2026-09 彻底移除，勿恢复；终止子智能体统一用 terminate_sub_agent |
| `core/main_terminal_parts/tools_execution.py` | 从 `_SUB_AGENT_SERIES_TOOLS`、`_READONLY_ALLOWED_TOOLS` 移除 |
| `core/tool_config.py` | sub_agent 分类移除该工具 |
| `utils/tool_result_formatter/agent_context.py`、`dispatch.py` | 删除 `_format_close_sub_agent` 及注册 |
| `static/src/utils/icons.ts`、`static/src/utils/avatarFace.ts`、`static/src/components/chat/MinimalBlocks.vue` | 删除图标/分组映射 |
| 检查 | `grep -rn close_sub_agent` 清零（历史对话记录走通用兜底渲染，不影响） |

### Phase 1：后端核心

**1a. 新建 `core/tool_loading.py`** — 延迟体系唯一权威：

- `RESIDENT_TOOLS`：常驻层清单（§3.1，不含条件性工具）
- `DEFERRABLE_REGISTRY`：类目 → {label, when_to_use, tools: […]}（§3.2）
- `deferrable_tool_names()`：注册表全部工具名（保序）
- `build_snapshot(deferred_config: list, disabled_tools: set) -> dict`：计算 enabled/deferred_set/initial_exposed/loaded/pending 五元组
- `reset_after_compression(state: dict) -> dict`：返回 `loaded=[]`、`pending=deferred_set` 的新 state
- `render_catalog(deferred_set) -> str`：按类目生成 prompt 目录（空类目省略）
- `is_tool_loading_enabled(metadata) -> bool`、`deferred_not_loaded(metadata, tool_name) -> bool` 等读侧 helper（防御性解析，字段缺失/损坏一律视为未启用）

**1b. 快照写入点**（创建即钉死）：

- 主落点：`utils/conversation_manager/crud_mixin.py::create_conversation`（Web REST、CLI、api_v1、runtime `_ensure_conversation_for_chat`/`create_session` 全部汇聚到此）——新建对话 metadata 写入 `tool_loading` 快照
- 排除：metadata 含 `multi_agent_mode=true` 时不写（多智能体 v1 不启用，等价老对话）
- 读取个人空间配置走 `load_personalization_config(data_dir)`（纯文件读，无 Flask，符合执行链红线）
- 验证点：确认「创建对话 → 首个请求 define_tools」的时序，保证首个请求前快照已写入（与 model_key 钉死时机一致；无对话的工作区级 terminal define_tools 回退现状全量）

**1c. `define_tools()` 改造**（`tools_definition/main.py`）：

- 从 `self.context_manager.conversation_metadata` 读 `tool_loading`
- 未启用（缺字段/enabled=false/多智能体）→ 现状全量逻辑
- 启用 → tools = 按 `initial_exposed` 过滤已构建定义 + 追加 view_image/view_video/vlm_analyze 条件逻辑（保持现状动态）+ 自定义/MCP 工具（现状）；`disabled_tools` 过滤逻辑不变
- `initial_exposed` 含 `load_tools` 时注入其定义（见 1d）

**1d. `load_tools` 工具**：

- 定义：`tool_names: string[]`（必填，1~N），description 说明"按名加载目录中列出的未加载工具，返回完整定义后即可直接调用；已加载的工具会重复返回定义"
- handler（`tools_execution.py`）：
  1. 读 metadata；未启用 → 错误提示
  2. `unknown = names − (loaded ∪ pending)` → 报错并附当前 pending 名单
  3. 重建这些名字的完整定义（复用 define_tools 构建产物按名筛选，含 intent 注入）
  4. `loaded ∪= names`、`pending = deferred_set − loaded`，经 `update_conversation_metadata` 落盘
  5. 返回 `{success, tools: [完整定义...], loaded_now, already_loaded}`
- **上下文 formatter 必须保留完整 JSON 定义**（定义靠 tool result 存续于上下文，这是 D1 的命脉；formatter 输出 = 简述 + 全量 JSON，不能只做摘要）
- 前端 `toolRenderers.ts` 渲染：名称列表 + 新加载/已加载状态徽章即可
- `_READONLY_ALLOWED_TOOLS` 加入 `load_tools`（只读发现性质，plan/只读模式可用）

**1e. 执行守门**（`tools_execution.py` 分发链前置，权限检查之后）：

- `tool_name ∈ deferred_set − loaded` → 不执行，返回引导错误：`工具 X 未加载。请先调用 load_tools(tool_names=["X"]) 获取定义后再调用。`（后端 i18n `tr()`，key 落在 `tools_execution.py` 消息文件）
- 已加载/非延迟工具 → 正常分发

**1f. prompt 文件与冻结拼接**：

- 新建 `prompts/tool_loading.txt`（静态骨架 + `{tool_catalog}` 占位符，内容见 §6）
- `context/messages.py`：skills 段之后插入新冻结段，key=`frozen_tool_loading_prompt`；**守卫**（照搬 AGENTS.md 段"不冻空串"模式）：仅当 metadata.tool_loading 启用且 deferred_set 非空时调用
- builder = 读 `prompts/tool_loading.txt` → `render_catalog(deferred_set)` 填占位符

### Phase 2：压缩重置

- `utils/context_manager/compression_mixin.py`：`_run_auto_shallow_compression`（浅度）与 `compress_conversation`（深度）完成摘要后，调用统一 reset：
  - `metadata.tool_loading` 存在且 enabled → `loaded=[]`、`pending=deferred_set`，`update_conversation_metadata` 落盘
  - 无字段 → 无操作
- 守门随之恢复拦截，模型下次用到时重新 `load_tools`（冻结 prompt 目录仍准确，无需动）

### Phase 3：个人空间 UI + i18n

- 后端：`modules/personalization_manager.py` 白名单 + sanitize 两个字段（bool + string[]，string 值 clamp 到注册表内）
- 前端 `static/src/stores/personalization.ts`：接口/默认值/sanitize 三处
- `PersonalizationDrawer.vue`（或现有分组内）新增「工具加载」设置区：
  - 总开关（switch）
  - 8 个类目的分组 checkbox（复用 `TOOL_CATEGORIES` 展示风格；类目级全选/全不选 + 工具级勾选）
  - 说明文案："仅对新对话生效；已有对话的工具集在创建时已固定"
- i18n：`static/src/locales/zh-CN/personalization.ts` + `en-US/personalization.ts` 新增 key；中文为源语言，en 缺 key 由 `DeepString` 编译约束兜底；新增文件不涉及 `i18n_baseline.txt`
- 后端 tr key：守门引导、load_tools 错误/成功提示（`modules/i18n_messages/tools_execution.py`）

### Phase 4：文档与收尾

- `AGENTS.md` 新增一节（工具动态加载）：注册表位置、快照语义、压缩重置、守门行为、**新增可延迟工具的 checklist**（注册表加条目 → 默认延迟集评估 → formatter/renderer → i18n）
- 检查 main_system.txt 中直接点名延迟工具的段落（记忆写入、个性化）：v1 不改（守门引导 + 目录文案已闭环，模型一轮即可学会）；后续观察再决定是否润色

## 6. prompts/tool_loading.txt 内容（定稿）

```text
## 按需加载的工具

本对话启用了工具动态加载：下列工具默认未加载。需要使用某类能力时，先调用 load_tools（tool_names 参数，一次可传多个工具名）获取完整工具定义，之后即可像普通工具一样直接调用。已加载的工具在本对话中持续可用，无需重复加载；未加载就直接调用会被拒绝并返回引导提示。

{tool_catalog}

使用建议：
- 用户意图明确属于某个类目时，先一次性加载该类目所需的全部工具再行动，避免边做边停
- load_tools 只接受上方列出的工具名；上方未列出的工具均已默认加载，无需处理
```

`{tool_catalog}` 渲染格式（每类目一行，仅列该对话实际延迟的）：

```text
- 工作流（activate_workflow, report_workflow_stage, ...）：按既定流程执行任务、创建/调整工作流、推进或查询已激活的工作流时
- 子智能体（create_sub_agent, ...）：并行处理独立任务、批量或后台执行、查询/终止子智能体时
…（其余类目同 §3.2 表）
```

## 7. 缓存安全性论证（设计前提复核）

1. tools 数组：`initial_exposed` 创建即钉死，对话终身不变 → 顶层声明稳定 ✓
2. system prompt：动态加载段经现有冻结机制懒冻结，终身不变 ✓
3. 工具定义加载：tool result 纯追加，不插入不修改中间消息 ✓
4. 个人空间改设置：只影响新对话快照，老对话 tools/prompt 均不动 ✓
5. 压缩：本身即前缀重建，reset 不引入额外缓存损失 ✓
6. 例外（现状固有，非本功能引入）：MCP/自定义工具增减、模型能力工具切换仍会引起 tools 数组变化，维持现状

## 8. 验证方案

### 自动化

- `python3 -m py_compile` 全部改动后端文件
- `python -m pytest test/test_server_refactor_smoke.py -q`
- 新增 `test/test_tool_loading.py`（unittest，无 Flask 依赖）：
  - snapshot 计算（默认配置/部分注入/空延迟集/禁用工具剔除）
  - reset_after_compression 语义
  - 守门判定（未加载拦截/已加载放行/非延迟放行）
  - load_tools handler（正常加载、重复加载、未知名字报错、未启用报错）
- 前端：`npm run build --silent 2>&1 | tail -n 5` + `npm run lint`（含 i18n 栏杆）

### 人工验收（用户重启服务后执行；**严禁我自行重启 8091/8092**）

1. 新对话：请求 tools 数组不含 19 个延迟工具；system prompt 含「按需加载的工具」段
2. 让模型"按工作流执行某任务"→ 应先调 `load_tools` 加载工作流类目 → 成功调用 activate_workflow
3. 子智能体场景：让模型派子智能体 → 先加载子智能体类目 → create_sub_agent 成功
4. 老对话（功能上线前创建）：全量工具、无目录段，行为与上线前一致
5. 个人空间关闭总开关 → 新建对话全量工具；重开 → 恢复
6. 个人空间改默认注入集 → 老对话 tools 数组不变；新对话按新配置
7. 触发一次对话压缩 → 之前加载的工具需重新 load，守门引导正常
8. token 对比：同一任务下日志中 tools 载荷大小前后对比，确认降幅 ≥40%

## 9. 实施顺序与 commit 边界

1. **Phase 0**：close_sub_agent 清理（独立 commit，conventional：`refactor(tools): remove zombie close_sub_agent`）
2. **Phase 1**：后端核心（registry + snapshot + define_tools + load_tools + 守门 + prompt 冻结 + formatter/renderer + 后端 i18n）——可通过手改 personalization.json 验证全链路
3. **Phase 2**：压缩重置
4. **Phase 3**：个人空间 UI + 前端 i18n
5. **Phase 4**：AGENTS.md + 全量验证 + 汇报

commit/push 均按 §6.2 单独征求用户许可。

## 10. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 模型不知道该用某延迟工具（目录发现失败） | 目录按类目+"什么时候用"组织；守门错误信息自带引导；观察期可调文案 |
| 模型盲调延迟工具 | 守门拦截并引导 load_tools，一轮自愈；不会执行错误参数 |
| 某创建路径漏写快照 | 该对话回退全量工具（安全方向），不会出现缺工具 |
| metadata 损坏/手改 | 读侧防御性解析，异常一律视为未启用（全量工具） |
| 回滚 | 个人空间总开关关闭 → 新对话即恢复全量；已启用对话状态自包含，无需迁移 |

## 11. v1 明确不做

- 多智能体对话的动态加载（主工具集为另一套）
- MCP `mcp__*` 工具延迟加载（机制预留，二期）
- kimi-k3 原生 system+tools 注入（通用机制已覆盖，无需私有路径）
- 对话级覆盖/中途变更 UI（改设置只影响新对话）
- `load_tools` 关键词模糊搜索（目录已承担发现）
- 前端"本对话已加载工具"可视化
