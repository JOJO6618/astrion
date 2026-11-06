# 用量统计页（设置 → 账户 → 用量统计，2026-11-06）

> AGENTS.md §26 的细则，与正文同级。改统计口径、数据来源、缓存或页面前先读本文。
> 若与代码冲突，以代码为准并回来同步本文与 AGENTS.md。

## 入口与布局

- 设置页新增「账户」分组下的 `usage` 分区（`/settings/usage`，前后端路由沿用 `/settings/<section>`，无需新登记）；该分区内容列放宽到 1040px（`settings-shell-content-inner--wide`），其余分区保持 760px。
- 个人空间抽屉「账户」页的原用量卡片保留（当前工作区、主智能体口径），新增「查看用量统计」按钮：桌面标签模式走 `conversationTabs.openPage('/settings/usage')`，其余整页跳转。
- 组件：`static/src/components/settings/tabs/UsageStatsTab.vue`（容器）+ `tabs/usage/`（`UsageActivity` 热力图/每周/累计、`UsageModelTable`、`UsageComposition`、`UsageCacheChart`、`UsagePunchCard`、`UsageTooltip`、`useUsageStats.ts`、`format.ts`、`types.ts`）。文案命名空间 `usageStats`（双语聚合器已注册）；日期、月份、星期用 `Intl` 按界面语言格式化，紧凑计数用 `Intl` compact（zh-CN 输出万／亿）。
- 配色：`_tokens.scss` 三主题＋首屏回退新增 `--heat-void`／`--heat-0..4`（经典陶土色阶，浅色/深色蓝色阶，深色越多越亮）与分类色 `--series-1..4`。热力图分档按非零值 25/50/80 分位，不用线性。
- 悬浮提示统一经 `tabs/usage/useUsageTooltip.ts`：文字一律主文字色（不用次要文字色）；任何滚动（设置页内容区、热力图横向滚动、窗口，捕获阶段监听）或窗口尺寸变化立即收起，并清除高亮格／柱／累计十字线。热力图键盘移动引起的滚动在下一帧按新位置重新显示。
- 视图偏好（粒度、指标、时间范围）只存本机 `localStorage`（`astrion_usage_stats_view`），读写失败回落默认；统计范围每次进入默认「全部工作区」。

## 数据来源与口径

- **唯一权威是对话费用账本** `<data_dir>/api_costs/<conversation_id>.json`（见 `docs/conversation_api_costs.md`）。统计覆盖账本里的全部执行者（主、子、思考摘要、审核智能体 `review_agent`，及后续计入账本的其它 kind），按 `actor.kind` 拆分；审核请求自 2026-11-06 起才入账。账本上线（2026-10-08）之前的请求没有时间戳，不补算；热力图以「无记录」底色区分。
- 总 Token = 输入（含缓存命中）+ 输出；「非缓存 Token」= 输入 − 缓存命中 + 输出。花费只累加 `status=priced` 的请求，未计价请求单独统计条数与 token，界面标注「另有 … 未计价」，**不按 0 计**。人民币只做显示换算（复用 `exchange_rates.get_rate()`），缺汇率显示「—」。
- 速度与首包只统计主智能体，口径同工作段 tokens/s：有 `speed_sample` 用样本（空对象跳过、不回退），否则用 `output_tokens / duration_ms`；模型级取 Σtoken/Σ毫秒，首包取平均 `ttft_ms`。

## 来源归集（`modules/usage_stats/sources.py` + `server/usage_stats.py`）

- 工作区列表经可信身份解析：host → `load_host_workspace_catalog()`；普通账户 → `user_manager.list_user_workspaces(prepare=False)`；API 用户 → `api_user_manager.list_workspaces()`。每个工作区用 `get_workspace_data(...)` 取 `data_dir` 与只读 ConversationManager 的 `conversations_dir`，**不创建 WebTerminal／容器、不选择工作区**。单个工作区解析失败只跳过并记 warning。
- Chat 账本根经 `server.chat_mode.context.principal()` + `scope_for_principal()` 解析（与 Chat 存储同一入口）。
- host 模式所有工作区共用 `DATA_DIR/api_costs`：按各工作区对话目录中的 `conv_*.json` 文件名归属；找不到归属（对话或工作区已删除）的账本归入「其它对话」（`ws:__unassigned__`），不丢费用、不猜归属。非共享账本（普通账户每个工作区独立 data_dir）整目录归该工作区，已删除对话的费用仍算在原工作区。

## 派生缓存（`modules/usage_stats/cache.py` / `rollup.py`）

- 每个账本目录同级维护 `<data_dir>/usage_stats_rollup.json`（version=1）：按账本文件 `(mtime_ns, size)` 判定是否重新解析，未变化直接复用；账本文件消失则丢弃对应汇总。缓存可随时删除重建，读失败的账本不写入缓存、下次重试。同一缓存文件进程内加锁，原子写。
- 汇总粒度 = UTC 小时 × `model_key` × `actor kind`；时区换算在出报表时完成（`report.py`）。前端传 IANA 时区名 `tz`，后端用 `zoneinfo` 解析；不可用（如 Windows 缺 tzdata）时回退浏览器给出的 `tz_offset`（分钟，东区为正）。非整点时区的半小时边界归入相邻小时，属已知近似。

## 接口

`GET /api/usage-stats?source=all|ws:<id>|ws:__unassigned__|chat&tz=<IANA>&tz_offset=<分钟>`（`@api_login_required`，仅 Web 主应用注册）。返回：`today`、`window_start`（53 周热力图起点）、`first_date`、`overview`（总量、花费、活跃天数、当前/最长连续、单日最高）、`days`（窗口内有用量的日期行，含当日主要模型）、`prior`（窗口前合计，供累计起点）、`ranges.{7,30,all}`（模型、kind、模式、来源、7×24 时段）、`sources`（全部来源及是否有数据）、`timezone`、`exchange_rate`。粒度、指标、时间范围切换均在前端完成，只有统计范围变化才重新请求；请求按代次判定归属，过期响应不写状态。

## 回归与验收

- 离线回归：`python3 -B -m unittest discover -s test/2026-11-06_用量统计页 -p 'test_*.py'`（合成账本、临时目录；覆盖分桶、未计价、速度样本、时区换日、连续天数、窗口前合计、共享账本归属、缓存复用/失效/删除）。
- 未在本机执行前端构建、类型检查与 lint（服务器内存限制）；三主题视觉、真实账本数据量下的首次扫描耗时、桌面标签跳转需用户验收。
