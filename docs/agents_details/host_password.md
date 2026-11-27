# Host 网页可选密码保护（完整）

> 本文由 `AGENTS.md` §21 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 21) Host 网页可选密码保护（2026-10-08）

- **默认免登录**：未运行密码脚本、缺少配置时沿用现有回环免登录。禁止按监听地址或推测公网状态强制初始化；存在但损坏的密码配置必须报错，不能静默降为免登录。
- **唯一开启/重设入口**：`./backend-rust/target/release/astrion-headless host-password --password 'YOUR_PASSWORD'`，密码通过参数直接指定（8–1024 字符，保留空白）。脚本使用服务同一配置解析的 `DATA_DIR`，可加 `--data-dir` 显式指定。每次执行开启保护并撤销旧网页登录会话；不依赖 Astrion CLI，不打印密码。
- **凭证存储**：`backend-rust/src/web_auth/credentials.rs` 管理 `<DATA_DIR>/host_auth.json`。命令重设与网页关闭共用跨进程锁，原子写入，Unix 权限为 0600。普通读取缺文件时不创建，写入拒绝符号链接。
- **散列格式**：Rust 使用 scrypt，参数为 n=32768、r=8、p=1、64 字节，沿用 Werkzeug 的格式和随机盐。原有密码可继续校验。
- **网页认证**：`backend-rust/src/web_auth/` 实现 `/host-login` 和设置页的关闭操作。nonce 与 generation 共同校验，密码状态变更撤销旧会话。启用后拒绝普通账户登录或注册，关闭后恢复现有回环访问限制。
- **桌面豁免**：以壳启动后端时注入的 `ASTRION_DESKTOP_VERSION` 判断，不能相信网页 JS、HTTP 头或 query 标记。Electron/Tauri 明确设置 `TERMINAL_SANDBOX_MODE=host`、`WEB_SERVER_HOST=127.0.0.1`、`ASTRION_IGNORE_DOTENV=1`；桌面不读取或应用 Host 密码配置，不显示密码管理。
- **本机 Bearer 独立**：`gateway_auth` 只注入请求内身份，不发行浏览器 nonce/Cookie。有效凭证只能在支持该装饰器的路由豁免 CSRF；无效 Bearer 不回退 Cookie。`security` after_request 清理临时 session 并阻止持久化，full/headless 共用；API v1 保留自己的认证。
- **运行任务保持**：密码变更不走 logout、不清理终端/任务，不取消正在运行的工作。新网页请求需按当前密码版本认证。
- **隔离回归**：`python3 -B test/2026-10-08_Host密码保护/run_regressions.py`，临时数据目录与合成凭证，禁读真实 `.env` 和密码/token；不启动服务。前端视觉、真实桌面启动及 Windows 文件锁行为仍需对应环境验收。
