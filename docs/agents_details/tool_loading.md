# 工具动态加载（完整）

> 本文由 `AGENTS.md` §13 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 13) 工具动态加载（tool_loading，2026-09 新增）

> 设计文档：`docs/dynamic_tool_loading_plan.md`。注册表与状态辅助唯一权威：`core/tool_loading.py`。

### 13.1 机制一句话

低频工具在顶层 `tools` 数组里只以 stub（名称 + 引导式短描述 + 空参数）常驻，system prompt 冻结段保留「类目+什么时候用+工具名」目录；模型调用前需先调常驻工具 `load_tools(tool_names=[...])`，在 tool result 里拿回完整 JSON 定义后直接调用；执行守门按对话 metadata 放行已加载工具。

### 13.2 硬约束（改代码必须知道）

1. **对话文件是唯一权威**：`metadata.tool_loading = {enabled, deferred_set, initial_exposed, loaded, pending}` 在**创建对话时快照一次**（注入点：`conversation_mixin.start_new_conversation`、`server/conversation.py` 活跃任务分支、`workflow_runtime_api.py`、`runtime/service.py`×2；多智能体对话不写入=不启用）。之后个人空间改动只影响新建对话；执行链路**禁止**为该功能读 personalization / Flask session。
2. **老对话无字段 = 未启用**：读侧一律走 `get_tool_loading_state()` 防御性解析（None 即未启用，全量工具），不做迁移。字段损坏同样按未启用处理。
3. **tools 数组对话级稳定（stub 常驻，2026-09-24 起）**：deferred 工具不整个剔除，而是替换为 stub（name + 一句引导式 description + 空 parameters，`core/tool_loading.py::build_tool_stub()`）——模型只敢调用「出现在工具列表里」的工具（训练先验，Answer.AI 实验与 Anthropic 官方 tool search 均证实），纯 tool result 塞定义曾导致所有测试模型反复 load 死循环。stub 只由完整定义决定、与加载状态无关，数组每轮逐字节一致，前缀缓存不受影响；loaded 后完整定义仍在 tool result 中，stub 不替换回完整定义。`initial_exposed` 只是首个请求懒记录的审计快照，不作为过滤依据。（历史：此前过滤语义为「构建集 − deferred_set」整个剔除，因上述死循环问题改为 stub。）
4. **prompt 目录走现有冻结机制**：key=`frozen_tool_loading_prompt`（`_get_or_init_frozen_prompt`，拼接在 skills 段之后）；模板 `prompts/tool_loading.txt` 只在冻结那一刻读一次。目录**不**随 loaded 状态变化（system prompt 是缓存首段，动了全破）。
5. **压缩必须重置**：深度压缩（`server/deep_compression.py`，in-place）、浅度压缩（`compression_mixin._run_auto_shallow_compression`）、手动压缩新建对话（同文件 `compress_conversation`）三处钩子统一做 `reset_state_after_compression`（loaded 清空、pending 回满 deferred_set）——load_tools 返回的定义活在 tool result 里，压缩后模型上下文已没有它们，不回滚守门会放行无定义调用。
6. **守门**：`tools_execution.py` 分发链前置——`deferred_set − loaded` 中的工具被调用时返回引导错误（`tools_exec.tool_not_loaded`），不执行；`load_tools` 本身在 `_READONLY_ALLOWED_TOOLS` 中（只读发现性质）。
7. **load_tools 的上下文 formatter 必须保留完整 JSON 定义**（`agent_context.py::_format_load_tools`）——定义靠 tool result 在上下文存续，只做摘要会导致历史重建后模型失去参数结构。
8. **close_sub_agent 已彻底删除**（2026-09，commit f6a46b5b）：它自出生就是 terminate_sub_agent 的别名，拆分重构时执行分支丢失成僵尸。勿恢复；终止子智能体统一用 terminate_sub_agent。
9. **重复加载直接拒绝**（2026-09-24）：load_tools 请求的工具全部已在 loaded 中时返回 `success=False`（`tools_exec.load_tools_all_already`），不再重复返回定义——成功返回曾诱导所有测试模型陷入「再加载一次」死循环（模型不敢调不在 tools 数组里的工具，只会反复 load）。配套 prompt 模板（`prompts/tool_loading.txt`）与 formatter 文案同步强调「不进 tools 数组也可直接调用，系统会正常执行」。**禁止**为解决这个问题把 loaded 工具追加回 tools 数组（破坏前缀缓存，用户明确否决）。最终定型方案 = 本条拒绝机制 + 第 3 条 stub 常驻。

### 13.3 新增可延迟工具 checklist

1. `core/tool_loading.py` 的 `DEFERRABLE_REGISTRY` 注册（类目 label + when_to_use 文案 + 工具名）；默认延迟集=注册表全集，无需另配。
2. 确认后端 formatter（`tool_result_formatter`）与前端 renderer（`toolRenderers.ts`）已覆盖该工具。
3. 个人空间勾选 UI 自动出现（注册表经 `/api/personalization` 的 `tool_loading_registry` 下发，类目标签 i18n key `personalization.toolLoadingCat.<key>` 需双语补齐）。
4. 跑 `test/历史测试_日期不明/test_tool_loading.py`（注册表完整性断言会校验数量与结构）。

---
