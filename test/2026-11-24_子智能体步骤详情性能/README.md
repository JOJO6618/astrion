# 子智能体步骤详情性能回归

本轮修复长单轮子智能体活动详情的增量读取与前端时间线计算。

- 后端：`modules/sub_agent/activity.py` 按字节游标分页读取 append-only `progress.jsonl`；`server/conversation.py` 的 activity 接口支持 `offset` / `before` / `limit`。
- 前端：`static/src/stores/subAgent.ts` 只拉新增行；时间线构建去二次方查找。
- 边界回归：`activity_paging_test.py`（空日志、半条记录、刚好整页、跨 64 KiB 块、超过一页积压、向前翻页、非法游标）。
- HTTP 契约回归：`activity_http_contract_test.py` 用真实 Flask app + 临时目录，逐字段对齐 `test/2026-11-24_Rust后端/sub_agents_contract.rs` 的活动接口 JSON。
- 前端行为回归：`subAgent_activity_behavior.test.mjs` 加载真实 Pinia store 与 `RunnerDetailPanel.vue`（Vite + runtime-core 轻量宿主），覆盖单飞、切换/关闭迟到响应、翻页期间继续收新步骤、20 万条数据与旧返回。
- 隔离数据：本目录脚本生成合成 `progress.jsonl`，不接触真实运行数据。

## 接口约定（Rust 对齐用）

```bash
node --test --test-concurrency=1 test/2026-11-24_子智能体步骤详情性能/subAgent_activity_behavior.test.mjs
```

`GET /api/sub_agents/<task_id>/activity`

| 参数 | 含义 |
| --- | --- |
| `offset=<bytes>` | 增量轮询：只返回该字节之后、同一 `stat` 快照内的完整行 |
| `before=<bytes>` | 向前翻页：返回紧邻该字节之前的完整行页；与 `offset` 互斥 |
| `limit=<n>` | 页大小。新客户端固定 `200`；带游标时服务端默认 200、上限 500；旧客户端无游标且不传时兼容 100000 |

响应字段：`entries` / `start_offset` / `next_offset` / `file_size` / `reset` /
`truncated` / `has_more_before` / `has_more_after` / `partial_tail`。

- 无游标：返回尾部一页；`has_more_before=true` 表示还能向前翻。
- `next_offset`：最后一条完整行之后的字节位置，永远落在换行边界；半行不消费、不推进。
- `has_more_after=true`：快照内还有完整行，前端应立即用同一 `limit` 继续拉，直到 false。
- `partial_tail=true`：末尾只有未完成半行，等下一轮轮询，不要立即重拉造成空转。
- `before=start_offset` 可连续向前翻页；`before=0` 返回空页且 `reset=false`。
- `reset=true`：游标非法或已过期，客户端应丢弃旧时间线并以本页尾部重建。
- `truncated` 恒为 false：不丢行；格式错误/非对象行跳过，但字节仍被消费。
- 所有读取以请求开始时的 `file_size` 为上限，之后追加的数据留到下一轮。
