# Web 认证、偏好与管理接口交接

本阶段停留在 `migration/rust-backend` 工作树，未提交。以下为已落盘改动、已有验证证据和下次继续时的验收边界。

## 管理策略与二次认证

- `backend-rust/src/admin_routes.rs` 和 `backend-rust/src/admin_routes/` 实现 `/api/effective-policy`、`/api/admin/policy` 和 `/api/admin/secondary/{status,verify}`，由 `host_web.rs` 装配到 Web 分支。
- `/api/effective-policy` 使用登录后的 Host 浏览器会话。策略管理还要求同一会话已完成二次认证。二次验证 POST 保留浏览器会话和 Origin 校验，其余写请求继续使用 CSRF。验证限频在同一 Host 用户下共用，每 300 秒最多 5 次，验证状态随登录会话变化和 TTL 到期失效。支持 `settings.json` 与兼容环境变量、Werkzeug PBKDF2/scrypt 哈希和明文配置。
- 策略写入与已有执行端共用 `<DATA_DIR>/admin_policy.json`，保留未识别的顶层字段。生效策略按全局、角色、邀请码、用户顺序合并，从现有模型目录和工具类别读取默认值。管理端使用与现有 `/terminal` 一致的实时终端策略。
- 自定义工具已下线。原管理路径返回 404，原工具目录和对话历史继续保存在数据根。

### 已有证据

- `backend-rust/target-master/recovery-build.log` 显示共享生产构建完成。`backend-rust/target-master/admin-http-agent.log` 记录 `test/2026-11-25_RustAdminRoutes/http_contract.py` 使用 `backend-rust/target-master/debug/astrion-headless` 通过，退出码 0。
- HTTP 夹具覆盖 Web 和 headless 启动、未登录重定向和 401、Origin 拒绝、同一浏览器登录会话绑定及 CSRF。策略保存后验证真实 `/terminal` 从 200 变为 403、禁用模型配置可读、重启恢复策略。原自定义工具路径和 Headless 管理路径返回 404。
- HTTP 夹具补齐了正常 Web 启动需要的 home 环境和 Vite manifest。已有通过结论来自实际二进制 HTTP 测试。

### 待验项

- `test/2026-11-25_RustAdminRoutes/admin_routes_contract.rs` 覆盖 WebAuth Cookie/CSRF、Host 跨会话限频、Werkzeug 哈希和 TTL、策略合并与旧字段保留，以及旧自定义工具路径和类别的移除。

## 已交付的相邻模块

- 浏览器偏好 REST 已接入运行时可信工作区注册表。测试覆盖会话隔离、注册变更、CSRF、站点授权撤销落盘及只读回收查询。Rust 原生桥缺失时 `/api/browser-tools` 如实返回 `supported: false`。
- CrossPet 配置和状态输出已接入真实 Host 导航、任务终态和子智能体活动。既有七项契约通过，禁用时注入 1,000 轮进度与唤醒触发 0 次工作线程通知。生产 Web 和 headless HTTP 已验收。
- CrossPet 订阅配额在独立线程复用供应商 OAuth、代理和每账号刷新锁。两项配额契约及 Host 合成凭据测试已通过。回环验收覆盖未登记旧凭据、损坏时保留 stale、换账号隔离迟到响应、关闭无新查询和登出清空。未访问真实账户。
