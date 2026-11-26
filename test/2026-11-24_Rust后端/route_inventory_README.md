# Python 与 Rust 路由清单

在仓库根目录使用已安装 `requirements.txt` 运行依赖的 Python 3.11 或更新版本。

```sh
python3.11 -B test/2026-11-24_Rust后端/route_inventory.py --check
python3.11 -B test/2026-11-24_Rust后端/route_inventory.py --print
```

`--check` 是默认模式。采集成功且快照一致时返回 `0`。存在差异时返回 `1` 并输出统一 diff，包含 HTTP method/path、挂载来源、Rust 声明和最新统计。采集或解析失败返回 `2`。确认变更后用 `--write-baseline` 更新 `route_inventory_baseline.tsv`，随后再次运行 `--check`。生成基线的环境为 CPython 3.11、Flask 3.1.0、Werkzeug 3.1.3。版本不参与快照比较。

## 采集范围

- Full 从生产入口 `server.app:app` 读取实际 `url_map`。它包含 Flask 的静态资源 Rule 和 29 个已注册蓝图。
- Headless 从生产工厂 `server.headless_app:create_headless_app()` 读取实际 `url_map`。工厂挂载 `gateway`、`tasks`、`model_catalog`、`status`、`approval`、`usage`、`providers`、`crosspet_settings` 和 `schedules` 九个蓝图。
- 两个入口都调用 `server.tasks.web:get_tasks_blueprint()`，由它导入任务 API 和技能模块后挂载。Headless 工厂另用 `add_url_rule` 直接挂入个性化、路径授权、命令拦截、子智能体列表、后台指令、工作流列表和会话版本检查点，并注册根路径告知页。TSV 表头记录实际蓝图和 Rule 计数，条目记录原始路径模板、Flask endpoint 和视图模块。
- 每个 profile 使用独立 Python 子进程、工作区、临时 HOME、数据目录和部署配置。子进程使用白名单环境、合成 Flask 密钥和空模型配置，禁读 `.env`，拦截网络绑定、连接和外部进程启动。`urllib3` 导入时的可选 IPv6 检测收到 bind 失败。脚本只导入或构造 Flask app，跳过共享 Host 初始化、后台任务和服务监听。错误处理器中的回退行为属于请求处理逻辑，TSV 仅以 `url_map` 中的 Rule 为准。
- Rust 扫描 `backend-rust/src/**/*.rs` 中的 Axum `.route(...)` 和路由属性，解析本模块路径常量、有限元组数组循环、直接方法链及 Web 资产的方法辅助函数。TSV 分别列出已识别的 Web 和 headless 装配状态，并保留每个来源。装配范围依据 `main.rs`、`gateway.rs` 和 `host_web.rs` 的入口连接。Web 会话列表和创建分别记录为 `GET /api/conversations` 与 `POST /api/conversations`。新增模块即使尚未接线，也会出现在源码列。引入新装配方式时先复核挂载范围，再更新解析器和基线。

## 核对口径

TSV 按路径和 HTTP method 排序，同一归一化 method/path 下可列出多项注册。变量名统一为 `{param}`，多段通配路径用 `{*param}`，`int` 和自定义转换器仍保留。主要方法统计排除 `HEAD` 和 `OPTIONS`，完整清单保留两者，Flask 自动生成的 `OPTIONS` 标为 `auto OPTIONS`。

| Rust 状态 | 含义 |
| --- | --- |
| `declared` | 已识别到该启动模式装配了精确的 HTTP method/path。 |
| `via_get` | Axum 由已装配的 `GET` 路由提供 `HEAD`。 |
| `wildcard` | 此路径使用 `any(...)`，以 `ANY` 单列。按方法覆盖统计只取明确的方法声明。 |
| `unconfirmed` | 源码有声明，清单尚未识别到生产入口装配。 |
| `missing` | 此启动模式无对应的精确声明。 |

| Python 范围及对应 Rust 模式 | Flask Rule | 方法与路径对 | 主要方法与路径对 | 其中 Rust 已装配 | 其余 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full 全部 → Web | 268 | 693 | 295 | 96 | 199 |
| Headless 全部 → Headless | 90 | 221 | 93 | 25 | 68 |
| Full `/api/` → Web | | 624 | 271 | 76 | 195 |
| Headless `/api/` → Headless | | 215 | 91 | 25 | 66 |

基线中 Rust Web 有 122 项明确的 method/path 装配，Rust headless 有 35 项。计入 GET 派生的 HEAD 后分别为 184 和 51。Rust Web 另有 2 条 `ANY`，Rust 源码另有 10 个尚未确认挂载的明确 method/path。迁移核对时先查看 `--check` 的行级 diff，再用对应的 Gateway 或 CLI 行为测试核验鉴权、状态码、请求与响应形状、存储、事件及副作用。

从仓库根目录运行真实 Rust 进程的 HTTP 样本验收。构建产物须支持 `ASTRION_DESKTOP_STOP_STDIN`，并可通过 `ASTRION_HEADLESS_BIN` 指定。

```sh
node --test test/2026-11-24_Rust后端/route_inventory_http.test.mjs
```

用例使用合成工作区、空计划、合成模型配置和回环监听。覆盖共用路由的 Bearer、Web Cookie、无凭据拒绝、计划 OPTIONS/HEAD、参数校验，以及 Web 会话创建的 CSRF、工作区、状态码与列表回读。
