# macOS arm64 本地启动基准：Python headless vs Rust headless

本目录只做**本机启动实测**，不改产品代码、不接触真实数据、不申请提权、不切换默认后端。
结论仅代表「本机、本数据、本二进制形态」，**不代表发行包/安装包性能**。

## 被测的两个 headless 入口

| 入口 | 命令 | 形态 |
|---|---|---|
| Python | `python3.11 -B -m server.headless_app --port P --path <ws>`（经 `py_bootstrap.py` 以 `__main__` 运行） | Homebrew **Python 3.11.13** + 仓库源码，Flask 开发服务器（`debug=False`） |
| Rust | `backend-rust/target/debug/astrion-headless --workspace <ws> --data-dir <data> --port P` | **debug** 构建，68,023,440 B，2026-11-25 22:03，未 strip；不带 `--web`（纯 headless） |

就绪判定：**同一路径 `/api/health` 首次给出 HTTP 响应**（连接层未就绪时连接失败）。

- 早期测量中，Python headless 对匿名 `/api/health` 请求返回 **401**。
- 当时被测的 Rust headless 二进制尚未挂载 `/api/health`，返回 **404**。后续版本已挂载该路由，对匿名请求返回 **401**。

这组数据测量进程首次响应 HTTP 的时间。原始状态码保存在 `result-*.json` 的 `ready_status`。Rust 的监听线
`Astrion Rust server listening on 127.0.0.1:<port>` 出现在全部初始化之后，也可交叉核对。

## 沙箱约束与停止方式（不涉及信号）

本机沙箱禁止父进程向子进程发任何信号（SIGTERM / SIGKILL 均 `EPERM`，`ps` 也被拒绝执行），所以停止方式避开信号：

- Rust：产品自带 `ASTRION_DESKTOP_STOP_STDIN=1` + stdin，EOF 后优雅退出；
- Python：`py_bootstrap.py` 起一个后台线程监视「停止文件」，出现即 `os._exit(0)`。

测量严格串行：每个进程退出并确认端口关闭后，才启动下一个；轮次顺序交替（偶数轮 Python→Rust，奇数轮 Rust→Python）。

## 隔离合成数据（`make_dataset.py`，无真实内容）

| 规模 | 对话数 × 每条消息 | 文件数 | 字节 | 备注 |
|---|---|---:|---:|---|
| small | 12 × 20 | 12 | 83,388（0.08 MB） | 新装/轻量旧数据 |
| large | 240 × 80 | 240 | 5,231,520（5.23 MB） | 模拟长期使用后的旧数据 |

数据由产品自己的 `ConversationManager.create_conversation()` 生成（走真实 schema），
工作区注册表 `host_workspaces.json` 只含 1 个合成工作区。不含真实对话、不含真实凭据。

## 结果（macOS arm64 本机，7 轮，含 1 轮预热不计入）

| 数据 | 入口 | p50 | 范围（min–max） | 就绪时 RSS（p50） |
|---|---|---:|---:|---:|
| small 0.08 MB | Python | **745.9 ms** | 701.0 – 821.5 ms | 111.1 MB |
| small 0.08 MB | Rust (debug) | **16.4 ms** | 12.7 – 17.1 ms | 16.5 MB |
| large 5.23 MB | Python | **776.9 ms** | 735.6 – 803.2 ms | 108.0 MB |
| large 5.23 MB | Rust (debug) | **15.1 ms** | 12.8 – 17.7 ms | 16.6 MB |

原始值（`result-small.json` / `result-large.json` 的 `summary.all_ms`）：

```
small python: 795.2 821.5 745.9 780.6 745.0 702.7 701.0
small rust  : 16.4 12.7 14.0 16.8 17.1 16.8 15.8
large python: 803.2 735.6 797.1 776.2 778.1 776.9 756.3
large rust  : 17.7 17.4 13.8 12.8 17.3 13.6 15.1
```

- 本机两个规模下 Rust/Python 比值约 **1:45 – 1:51**。
- **数据规模对“到 HTTP 就绪”几乎无影响**（Python +31 ms / +4%，Rust −1.3 ms / −8%，均在轮间波动内）：
  两个入口启动期都不扫历史对话（Rust `main.rs` 只做 runtime/catalog/工作区工具绑定/workflow 注册；
  Python `initialize_host` 只做 token 与默认工作区）。历史数据量影响的是首个列表/状态请求，不在本次口径内。

## RSS 读取方式

沙箱里 `ps` 被拒绝（`/bin/ps: Operation not permitted`），改用 libproc 的 `proc_pidinfo(pid, PROC_PIDTASKINFO)`，
取 `proc_taskinfo.pti_resident_size`（偏移 8，uint64）。对同一 PID 与 `ps` 口径一致，读不到时返回 `null`。
数值为**就绪那一刻**的 RSS，不代表稳态峰值。

## 复跑

```sh
python3.11 -B test/2026-11-26_Rust启动性能/make_dataset.py \
  --root test/2026-11-26_Rust启动性能/.tmp/small --conversations 12 --messages 20
python3.11 -B test/2026-11-26_Rust启动性能/startup_bench.py \
  --root test/2026-11-26_Rust启动性能/.tmp/small --rounds 7 --warmup 1 --label small \
  --json-out test/2026-11-26_Rust启动性能/result-small.json
```

注意：同一个数据根只能被一个后端占用（`astrion-backend.lock` 用 flock）。
若某次运行被中断、进程未退出，该数据根会一直显示「Another Astrion backend is using this data directory」，
请换一个新根重跑（本次就遇到过一次：早期冒烟中断留下过一个孤儿进程持着 `.tmp/small` 的锁，
该进程已在 2026-11-26 由主侧在沙箱外回收，见 `ORPHAN.md` 的历史记录；正式测量使用全新的
`small2` / `large` / `biz-*` 根，不受影响）。

## 局限（读结论前必读）

1. **这不是发行包数据**：Rust 是 debug 二进制（未优化、未 strip），Python 是 Homebrew 3.11 + 仓库源码 + Flask 开发服务器；
   产品实际用的是内嵌 python-build-standalone 3.12，本工作树没有该 staging，无法在此口径复现。
2. 早期两端的匿名探针分别得到 401 和 404。下文的业务复测统一要求带鉴权的会话列表返回 200，并校验列表内容。
3. 数据为合成数据；规模只覆盖 0.08 MB 与 5.23 MB 两档，且已证实不影响本口径。
4. 轮次间共享同一数据根，文件页缓存是热的；没有测冷启动（重启后首次读盘）。
5. RSS 是就绪瞬间值；没有测稳态、峰值或长期运行内存。
6. 早期冒烟中断曾留下 1 个孤儿 Python 后端（持 `.tmp/small` 锁）；该进程已于 2026-11-26 在宿主被回收
   （PID 17854，端口 56568 已关闭），回收前它处于空闲态，对测量只有可忽略的内存/页缓存影响，
   且测量轮次自身严格串行。回收经过见 `ORPHAN.md`。

## 复测（同一真实业务端点，2026-11-26 06:1x 本地）

上面 401 / 404 的口径不再作为交付结论。本段改为**两端都访问同一真实业务端点**
`GET /api/runtime/sessions`，带合成 Bearer（读 `<data-dir>/host_api_token`，两端启动时各自生成/复用），
判定条件更严：**HTTP 200 且 JSON `success=true` 且 `conversations` 为 list**。业务字段由同一份合成数据决定，
两端返回的键集合与 `total` 完全一致，可交叉验证是同一业务路径而不是各回各的空壳。

| 数据 | 入口 | p50 | 范围（min–max） | 就绪时 RSS（p50） |
|---|---|---:|---:|---:|
| small 0.08 MB（12 对话） | Python 3.11.13 | **1070.8 ms** | 1053.2 – 1085.5 ms | 109,568 KB（107 MB） |
| small | Rust debug | **19.3 ms** | 16.5 – 20.2 ms | 19,840 KB（19.4 MB） |
| large 5.23 MB（240 对话） | Python 3.11.13 | **1050.0 ms** | 1039.9 – 1066.9 ms | 113,232 KB（110.6 MB） |
| large | Rust debug | **32.7 ms** | 29.1 – 33.9 ms | 21,776 KB（21.3 MB） |

业务体校验（两端逐轮均 200）：

- small：`keys = [conversations, has_more, limit, offset, success, total]`，`items=12, total=12`；
- large：同键集合，`items=50, total=240`（默认 `limit=50`，`total` 是全量，说明两端都真的读到了 240 个对话）。

被测 Rust 二进制：`backend-rust/target-master/debug/astrion-headless`
（77,679,072 B，mtime 2026-11-26 06:11:26，
`sha256=9b4f3e8c7417d55dc7bcb9a658f9afe1bce87188f0da292e49a299941b65f72c`）。
原始数据：`result-biz-small.json` / `result-biz-large.json`（含全部轮次、`ready_status`、`first_ready_body`、二进制 hash）。

复跑：

```sh
python3.11 -B test/2026-11-26_Rust启动性能/startup_bench.py \
  --root "$PWD/test/2026-11-26_Rust启动性能/.tmp/biz-small" --rounds 7 --warmup 1 --label biz-small \
  --json-out test/2026-11-26_Rust启动性能/result-biz-small.json
```

### 本段新增的局限（比上一段更该看）

1. 与上一段口径**不可混比**：上一段把「首次给出任意 HTTP 响应」当就绪（401/404 即算），本段要等 200 + 业务字段，
   Python 因此从 ~750 ms 抬到 ~1050 ms（多了鉴权、路由、列表装配与首个列表请求成本）。**两组数字都只代表本机**，
   不是安装包、不是内嵌 3.12 运行时的数据。
2. Rust 仍是 **debug** 构建（未 strip、未优化），发行包会明显更小也更快；Python 是 Homebrew 3.11 + Flask 开发服务器。
3. 就绪判定靠 10 ms 间隔轮询，采样分辨率 ±10 ms：Rust 的 16.5–33.9 ms 区间里，粒度误差占比不小。
4. 两端共享同一数据根、页缓存已热，未测冷启动；RSS 为就绪瞬间值。
5. 只测到「列表可返回」这一层，未包含首个 UI 渲染、流式连接与长任务弹窗数据链路。

### 逐轮总数校验（事后补强）

原始 `result-biz-small.json` / `result-biz-large.json` 的**就绪判定只校验结构**（200 + `success=true` + `conversations` 是 list），
没有把 `total` 与期望对话数比对；但每轮的 `ready_body` 都记了 `items` / `total`，事后逐条核对结果如下：

- small：14/14 次启动（7 轮 × 2 端）`total=12`、`items=12`；
- large：14/14 次启动 `total=240`、`items=50`（`limit=50` 截断，`has_more` 应真）。

为了让这件事由代码而不是人工核对保证，`startup_bench.py` 新增了 `--expect-total`：
命中 200 且结构成立后，再比较 JSON `total`，不符即本轮失败（`total mismatch: got X, expect Y`）。
用它重跑了 3 轮 × 2 端 × 2 规模（写进 `result-biz-small-verify.json` / `result-biz-large-verify.json`），
**全部 12 次启动都通过总数校验，没有出现 mismatch**。

注意：verify 那批的耗时（Python 1260–1840 ms、Rust 18–36 ms）是同期机器负载较高时测到的，**不能当性能数据引用**，
它只证明「每一轮返回的总数都对」。性能结论仍用上面 7 轮那两张表。
