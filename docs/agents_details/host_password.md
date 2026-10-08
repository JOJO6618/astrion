# Host 网页可选密码保护（完整）

> 本文由 `AGENTS.md` §21 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 21) Host 网页可选密码保护（2026-10-08）

- **默认免登录**：未运行密码脚本、缺少配置时沿用现有回环免登录。禁止按监听地址或推测公网状态强制初始化；存在但损坏的密码配置必须报错，不能静默降为免登录。
- **唯一开启/重设入口**：`python3 scripts/host_password.py --password 'YOUR_PASSWORD'`，密码通过参数直接指定（8–1024 字符，保留空白）。脚本使用服务同一配置解析的 `DATA_DIR`，可加 `--data-dir` 显式指定。每次执行开启保护并撤销旧网页登录会话；不依赖 Astrion CLI，不打印密码。
- **存储唯一权威**：`modules/host_auth.py`，`<DATA_DIR>/host_auth.json`（版本、enabled、scrypt 哈希、generation），0600、原子写；脚本重设与网页关闭共用跨进程文件锁。缺文件只读不创建；脚本可以修复损坏的常规配置，不写穿符号链接。
- **scrypt 实现兼容**：`modules/host_auth_hash.py` 使用项目已有 `cryptography` 的 Scrypt，固定 n=32768/r=8/p=1/64 字节，保留 Werkzeug 哈希格式与随机盐，恒定时间比较。生成和校验都不依赖 `hashlib.scrypt`（本机 Apple Python 3.9 缺失该属性，pip 不能补齐解释器内置能力），不能只修脚本而漏掉登录校验。与原 Werkzeug 哈希双向互通；回归 `test/2026-10-08_Host密码保护_02/`，实测 3.9/3.11 脚本与校验互通，不代表项目整体支持 Python 3.9。
- **网页认证**：`server/host_password.py` 管策略；`server/auth.py` 的 `/host-login` 接收密码，启用后允许远程密码认证；关闭时保留现有回环限制。登录 nonce 与 generation 共同校验，开启/重设/关闭令旧浏览器 session 失效。启用时隐藏并拒绝普通账户登录/注册，旧账户会话也不能绕过。设置→通用只提供验证当前密码后关闭；没有网页开启/重设接口。
- **桌面豁免**：以壳启动后端时注入的 `ASTRION_DESKTOP_VERSION` 判断，不能相信网页 JS、HTTP 头或 query 标记。Electron/Tauri 明确设置 `TERMINAL_SANDBOX_MODE=host`、`WEB_SERVER_HOST=127.0.0.1`、`ASTRION_IGNORE_DOTENV=1`；桌面不读取或应用 Host 密码配置，不显示密码管理。
- **本机 Bearer 独立**：`gateway_auth` 只注入请求内身份，不发行浏览器 nonce/Cookie。有效凭证只能在支持该装饰器的路由豁免 CSRF；无效 Bearer 不回退 Cookie。`security` after_request 清理临时 session 并阻止持久化，full/headless 共用；API v1 保留自己的认证。
- **运行任务保持**：密码变更不走 logout、不清理终端/任务，不取消正在运行的工作。新网页请求需按当前密码版本认证。
- **隔离回归**：`python3 -B test/2026-10-08_Host密码保护/run_regressions.py`，临时数据目录与合成凭证，禁读真实 `.env` 和密码/token；不启动服务。前端视觉、真实桌面启动及 Windows 文件锁行为仍需对应环境验收。
