# 子智能体长日志性能交接

截至暂停指令，前端长日志数据通路已完成收口，后端 Rust 未作改动。本文件只汇总已有前端性能成果与实际跑过的验证，不包含新的测试执行。

## 结论

长日志慢的直接链路已从“每次轮询整份活动 + 全量渲染”改为“按字节游标增量取页 + 前端硬上限”。生产数据通路现在保证：

- 单次活动响应最多只保留尾部 `PAGE_SIZE = 200` 条；
- 详情面板可见条目最多 `VISIBLE_ENTRY_LIMIT = 400` 条；
- 旧后端一次性返回 20 万条时，不再把 20 万条交给模板渲染；
- 历史回看期间实时页继续接收，但保留在后台，返回最新时合并显示；
- 旧后端不支持 `offset` 时，实时轮询用尾部页替换而不是重复追加。

## 生产改动

文件：`static/src/stores/subAgent.ts`

- 新增 `VISIBLE_ENTRY_LIMIT = PAGE_SIZE * 2`。
- `activityEntries` getter 增加防御性尾部截断，作为所有路径的最后上限。
- 活动响应统一裁剪：
  - `received.length > PAGE_SIZE` 时只保留 `received.slice(-PAGE_SIZE)`；
  - 新后端正常 200 分页时该操作等价于原数组。
- legacy 判定：响应没有 `start_offset` 时视为旧后端。
  - 旧后端忽略 `offset`、每轮仍返回尾部页时，`livePages = [page]` 替换，不再每 2 秒重复堆同一批数据；
  - 连 `next_offset` 都没有时，`liveOffset = null`，退化为最后一页。
- 现代分页语义保持：
  - `offset` 增量；
  - `before` 历史页；
  - `historyPage` 回看期间实时页继续接收；
  - 返回最新（`historyPage = null`）后显示合并后的实时页。

未修改 `RunnerDetailPanel.vue`；组件仍消费 `activityEntries`，通过 store 上限获得有界渲染。

## 实际验证记录

测试文件：`test/2026-11-24_子智能体步骤详情性能/subAgent_activity_behavior.test.mjs`

执行命令：

```bash
node --test --test-concurrency=1 test/2026-11-24_子智能体步骤详情性能/subAgent_activity_behavior.test.mjs
```

结果：**12/12 通过**，Node 报告约 `1.09s`。

测试使用真实 Pinia store 与真实 `RunnerDetailPanel.vue`（Vite SSR 加载 store、`@vue/compiler-sfc` client 模式编译组件、`@vue/runtime-core` 轻量宿主），不启动浏览器、不并发构建。

覆盖并通过：

| 场景 | 结果 |
| --- | --- |
| 慢活动请求单飞 | 首次只发 1 个请求；请求中再次调用被忽略；完成后只应用一次 |
| 切换子智能体 | 旧请求迟到响应被丢弃，不覆盖新智能体 |
| 关闭详情 | 请求被 `abort`，迟到响应不落状态 |
| 切换对话 | 旧对话迟到响应失效 |
| 历史回看继续接收 | `historyPage` 保留时实时页继续接收；返回最新后可见新步骤 |
| 实时页上限 | `livePages` 严格保持两页 / 400 条 |
| 20 万条 modern payload | `storeEntries=200`、`domRows=200`，上限 400 |
| 旧返回无 `start_offset` | 归一为一页 200 条 |
| 旧后端忽略 `offset` | 尾部页替换，不重复累积 |
| 旧返回无 `next_offset` | 退化为最后 200 条 |
| 组件切换迟到行 | 旧智能体迟到行不渲染到新智能体 |
| 组件关闭迟到行 | 关闭后迟到行不渲染 |

修复前后对照（同一条 20 万条有界用例）：

```text
修复前：bounded rendering failed: storeEntries=200000, domRows=200000, limit=400, elapsed=2900ms
修复后：通过，storeEntries=200, domRows=200
```

## 构建验证

```text
./node_modules/.bin/tsc --noEmit   → exit 0
npm run build                      → exit 0；vite built in 6.87s
```

`npm run build` 只在当前工作树生成 `static/dist` 构建产物，未改 Rust。

## 关联基线（已存在，非本轮前端结论）

- `test/2026-11-24_子智能体步骤详情性能/activity_paging_test.py`：7/7 通过，覆盖空日志、半条记录、整页跨 64 KiB 块、超过一页积压、向前翻页、非法游标。
- `test/2026-11-24_子智能体步骤详情性能/activity_http_contract_test.py`：4/4 通过，用真实 Flask app + 临时目录逐字段对齐 Rust 活动接口 JSON。

## 未覆盖与风险

- 未做真实浏览器 DOM/视觉验收；组件测试是轻量 runtime-core 宿主，不是 Chromium/WKWebView。
- 有界渲染靠尾部截断，不是虚拟滚动；20 万条只显示最新 200 条，更早历史需靠后端 `before` 翻页，旧后端无 `start_offset` 时只能看到尾部页。
- 旧后端不支持 `offset` 时，实时轮询无法真正增量，只能每轮取尾部页替换。
- 临时探测（未纳入正式测试）：2 万条 tool 行在旧的无界路径下曾约 30s，原因是时间线按 key 去重时使用 `rawItems.some`；当前 200/400 上限已把该路径限制在小规模，但若未来放开上限，需要重新评估该二次方逻辑。
