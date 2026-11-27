# 技能（AgentSkill）名称注入与检索

> 来源：`AGENTS.md` §29（2026-11-19 新增）。改技能注入、`search_skills`、`read_skill` 前先读本文。

## 机制一句话

系统提示词只注入**已启用技能的名称**（按目录分组，不含 description）；用途说明靠常驻工具 `search_skills` 检索 SKILL.md 获得，命中后用 `read_skill` 读全文。不设开关，旧的「名称＋描述」全量列表已移除。

## 注入（名称列表）

- 唯一生成点 `modules/skills_manager.py::build_skills_list(catalog, enabled_ids)`，返回行列表：
  - 每个目录一行：`.astrion/skills/：a, b, c`、`.agents/skills/：x, y`（按首次出现顺序）。
  - 同名冲突末尾单独一行：`同名重复（read_skill 会报错，请用 read_file 按具体路径读取）：id（.astrion/skills/ 与 .agents/skills/ 各一份）`。
- 主智能体：`core/main_terminal_parts/context/system_prompt.py::_skills` → 模板 `prompts/main_agent/dynamic/skills.txt`，进入冻结系统提示词快照（只在新建／压缩重建时生成；已有对话保留旧列表直到下次压缩，但新工具立即可用）。
- 子智能体：`modules/multi_agent/sub_agent_context.py::_build_skills_section`，列全部技能（不区分启用），文案内联在该函数。
- 固定使用规则在 `prompts/main_agent/static.txt`「## Skills 使用规则」：产出型任务先检索、关键词带英文、命中后必须 read_skill 读全文、未命中不换同义词重试。
- 旧模板 `prompts/skills_system.txt` 已删除（无引用）。

## Rust Host：工作区技能准备

- Web 设置保存只即时更新当前服务端解析的工作区；其它已登记工作区下次打开技能选择或发送 Agent 任务时，按同一账号的当前设置准备。直接发起的任务也会在生成新系统提示词前准备，不依赖先打开技能选择。
- 软件内置库与个人库同名时以个人库为准，归档新技能会让其他工作区在下次使用时刷新。副本在工作区 `.astrion/skills`，不会改动 `.agents/skills`。
- 只有 Host 记录且内容仍一致的副本会更新或清理。手工放入的目录和改过的副本保留原文件；已禁用技能不在输入栏和模型检索中列出。同步失败不替用户覆盖目录，设置与任务分别给出警告。

## 检索工具 `search_skills`

- 定义唯一来源：`modules/skills_manager.py` 的 `SEARCH_SKILLS_DESCRIPTION` / `SEARCH_SKILLS_PARAMETERS`，主（`tools_definition/file_tools.py`）与子（`modules/sub_agent/toolkit.py`）共用，改文案只改一处。
- 执行：`core/main_terminal_parts/tools_read.py::_handle_search_skills`；分发在 `tools_execution.py`（参数清洗同 `search_project_memory`）。子智能体经子终端 `handle_tool_call` 走同一实现。
- **检索范围 = read_skill 可读范围**：两者共用 `_enabled_skills_catalog()`（执行后端遍历 + 控制端启用过滤）。禁止让检索返回 read_skill 读不到的技能。目录读取失败返回错误，不回退控制端本机遍历。
- 评分沿用项目记忆检索：id/frontmatter name 命中 +10，description +5，正文每行 +1（单关键词最多 5 行）；忽略大小写；关键词去重后最多 5 个，`max_results` 默认 5、范围 1–10。
- 返回：`results[]` = `skill_id / path / description（截断 300 字）/ conflict_path / score / matched_keywords / snippets（≤3 条，行号基于完整 SKILL.md，单条 ≤120 字）`，`content` 为预格式化文本（formatter 原样使用）。
- 纯子串匹配，**中文关键词搜不到英文正文**（实测「表格」零命中，「spreadsheet, xlsx」命中 xlsx）——这是提示词要求带英文词的原因，不要删该规则。
- 只回片段，**不进** read-before-edit 已读白名单（同 `search_project_memory`）；属只读工具，已加入 `_READONLY_ALLOWED_TOOLS`。

## 注册清单（新增／改名时同步）

- 工具设置中的 `skills` 类别负责技能工具的启用状态。
- 上下文 formatter：`utils/tool_result_formatter/dispatch.py`；子通信 formatter：`modules/sub_agent/toolkit.py::_format_tool_result`。
- i18n：`modules/i18n_messages/tools_read.py`（`tools_read.skills_*`）、`tools_execution.py`（`tools_exec.search_skills_needs_keywords`）。
- 前端：`toolRenderers.ts::renderSearchSkills`、`subAgentToolText.ts`、`MinimalBlocks.vue`（归入 `search` 摘要）、`chatDisplay.ts` 四态、`icons.ts`、`avatarFace.ts`、`stores/monitor.ts`；文案 `toolResults.*.searchSkills`、`toolResults.counts.skillHits`、`toolResults.sentences.noMatchesSkill`、`quickdock.toolSearchSkills`（中英双语）。
- 未做动态加载延迟：`search_skills` 与 `read_skill` 一样常驻。

## 已知差异

- 子智能体名称列表不区分启用状态，但 read_skill / search_skills 按启用过滤；子智能体可能看到已禁用技能的名字却读不到（本次之前就存在，未改）。

## 验证

```bash
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-11-19_技能检索 -p 'test_*.py'
```
