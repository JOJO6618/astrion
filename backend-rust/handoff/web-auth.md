# Web 认证、偏好与管理接口交接

本阶段停留在 `migration/rust-backend` 工作树，未提交。以下为已落盘改动、已有验证证据和下次继续时的验收边界。

## 管理策略与二次认证

- `backend-rust/src/admin_routes.rs` 和 `backend-rust/src/admin_routes/` 实现 `/api/effective-policy`、`/api/admin/policy`、`/api/admin/secondary/{status,verify}`、`/api/admin/custom-tools`、`/api/admin/custom-tools/{file,reload}`。主侧已在 `lib.rs` 注册模块，在 `host_web.rs` 装配到 Web 分支。
- `/api/effective-policy` 使用登录后的 Host 浏览器会话。策略管理与工具管理还要求同一会话已完成二次认证。二次验证 POST 按 Python 契约豁免 CSRF，并保留浏览器会话和 Origin 校验。其余写请求继续使用 CSRF。验证限频在同一 Host 用户下共用，每 300 秒最多 5 次，验证状态随登录会话变化和 TTL 到期失效。支持 `settings.json` 与兼容环境变量、Werkzeug PBKDF2/scrypt 哈希和明文配置。
- 策略写入与已有执行端共用 `<DATA_DIR>/admin_policy.json`，保留未识别的顶层字段。生效策略按全局、角色、邀请码、用户顺序合并，从现有模型目录和工具类别读取默认值。管理端使用与现有 `/terminal` 一致的实时终端策略。
- 自定义工具管理对原有 `definition.json`、`execution.py`、`return.json` 和 `meta.json` 执行真实存储操作，注册表在新增、删除和显式重载时刷新。工具数据可见于同一管理 API 和生效策略的 `custom` 类别。文件接口限制在四种工具文件内，读取和写入拒绝工具目录内的符号链接。当前 Rust 模型运行时的 `ToolSet` 缺少具有 Python `terminal_ops` 沙箱和权限语义的脚本执行器，因此自定义脚本依然无法由模型调用。管理 API 的 `execution.type` 表示已保存的脚本类型。

### 已有证据

- `backend-rust/target-master/recovery-build.log` 显示共享生产构建完成。`backend-rust/target-master/admin-http-agent.log` 记录 `test/2026-11-25_RustAdminRoutes/http_contract.py` 使用 `backend-rust/target-master/debug/astrion-headless` 通过，退出码 0。
- 实际监听器覆盖 Web 和 headless 启动。Web 覆盖未登录重定向和 401、Origin 拒绝、无需 CSRF 的二次验证、同一浏览器登录会话绑定、缺 CSRF 写请求拒绝、保存策略后真实 `/terminal` 从 200 变为 403、读取已禁用模型、工具落盘与显式重载、目录越界拒绝、重启恢复策略和工具、DELETE 的 CSRF。Headless 入口对这些管理路径返回 404。
- HTTP 夹具补齐了正常 Web 启动需要的 home 环境和 Vite manifest。已有通过结论来自实际二进制 HTTP 测试。

### 待验项

- `test/2026-11-25_RustAdminRoutes/admin_routes_contract.rs` 有四项 Rust 契约，覆盖真实 WebAuth Cookie/CSRF、Host 跨会话限频、Werkzeug 哈希和 TTL、策略合并与旧字段保留、工具重载及符号链接保护。这组独立测试尚无通过记录。此前编译整库测试时，工作树中另有未完成的 Chat 和 Git 模块及夹具，导致未能运行到该组。
- 真实模型工具调用需要先接入受信脚本执行层，再验工作区作用域、审批与沙箱、参数与超时、错误返回和重载后可见性。当前交付覆盖管理和注册，不声明脚本可执行。运行时模型与工具对各类策略的全面执行不在已通过的 `/terminal` 验收范围内。

## 已交付的相邻模块

- 浏览器偏好 REST 已接入运行时可信工作区注册表。测试覆盖会话隔离、注册变更、CSRF、站点授权撤销落盘及只读回收查询。Rust 原生桥缺失时 `/api/browser-tools` 如实返回 `supported: false`。
- CrossPet 配置和状态输出已接入真实 Host 导航、任务终态和子智能体活动。既有七项契约通过，禁用时注入 1,000 轮进度与唤醒触发 0 次工作线程通知。生产 Web 和 headless HTTP 已验收。
- CrossPet 订阅配额在独立线程复用供应商 OAuth、代理和每账号刷新锁。两项配额契约及 Host 合成凭据测试已通过。回环验收覆盖未登记旧凭据、损坏时保留 stale、换账号隔离迟到响应、关闭无新查询和登出清空。未访问真实账户。
