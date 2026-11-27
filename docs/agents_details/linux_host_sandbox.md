# Linux host 沙箱（完整）

与 `AGENTS.md` §20 同级。首批 Ubuntu 24.04 隔离机制于 2026-10-08 验证，Rust 生产链保留固定系统边界与任务回收契约。

---

## 20) Linux Host 沙箱

- **独立安装入口**：普通账户执行 `cargo build --locked --release --manifest-path backend-rust/Cargo.toml -p astrion-linux-sandbox`，再运行 `sudo bash scripts/setup-linux-sandbox.sh`。默认授权 `SUDO_USER`，root 直接安装须加 `--user <普通账户>`。支持 `--dry-run`、`--status`、`--no-install-dependencies`；安装不启动 Astrion 后端。安装器由独立二进制的 `install` 子命令运行。
- **首批环境**：Ubuntu 24.04、kernel 6.8、systemd 255、cgroup v2、AppArmor、libseccomp。安装器可装系统依赖，并在发布服务前核对 bubblewrap 支持 `--bind-fd` 和 `--ro-bind-fd`。不具备所需能力时停止安装。禁止关闭 AppArmor、放宽 userns 限制或回退到 direct。生产全局安装与网页 sudo 仍需实机验收。
- **可信助手**：服务运行 `/usr/local/libexec/astrion-sandbox/astrion-linux-sandbox serve /etc/astrion-sandbox/helper.json`。二进制、配置和 BPF 均由管理员安装并保护。成功升级保留旧助手备份，验收失败回滚旧文件及服务状态。Rust 后端需要 v2 助手，旧安装须经过原安装入口升级。
- **调用与身份**：普通客户端通过固定 SEQPACKET socket 传严格 JSON 和三个 stdio FD。broker 用 SO_PEERCRED、SO_PEERSEC 与 SO_PEERPIDFD 核验身份和生命周期，拒绝 UID0 及沙箱进程的递归调用。UID、groups、助手路径和 unit 属性只能来自内核或可信配置。用户命令只能在 Rust 入口降到真实普通 UID/GID、清除能力、开启 NNP、关闭继承的挂载和控制描述符后运行。
- **文件根**：系统目录和精确系统文件清单由 `backend-rust/linux-sandbox/src/protocol.rs` 定义，主/子/只读/后台/持久终端统一走 Rust 命令计划。源在普通用户 fork 中逐组件 no-follow 打开并固定 FD，cwd 降权后再验设备与 inode。`/proc`、`/dev`、`/sys` 和内部 runtime 禁止作为宿主挂载授权。shell 的临时 `/tmp`、HOME 不增加原生文件工具的宿主路径授权。额外路径授权每次读取现行工作区政策。最低子档的持久写入只允许指定的工作目录。
- **网络与回收**：共享 host 网络；每任务独立 transient unit，restricted=deny any+allow localhost，none=deny any。runner 检查 enforcing 标签和实际 ingress/egress BPF，缺失则拒绝。受限档拒绝抽象 Unix socket；none 通过独立 seccomp 禁止新 Unix socket 和 datagram socketpair，保留匿名 stream/seqpacket IPC。客户端断开/pidfd 退出、助手停止和取消均回收本任务 unit；启动前二次握手与可撤销 lease 防迟到执行。安装后 unit 绑定 broker 服务生命周期。
- **网页/headless**：现有沙箱向导通过 `/api/sandbox/*` 路由调用独立安装脚本。仅在脚本与二进制都位于管理员拥有的路径且 sudo 已授权该命令时，提供无交互安装。其余情况显示终端命令。网页不收 sudo 密码。三端点沿用现有 host Cookie/Bearer 认证。
- **证据与限制**：`test/2026-11-27_Linux_Rust沙箱/` 覆盖 Rust 请求及 Linux 非特权传输边界。迁移前的运行证据在 `test/2026-10-08_Linux_host沙箱实验/`、`test/2026-10-08_Linux_host沙箱助手/`、`test/2026-10-08_Linux_host沙箱适配/`，历史 Python 对照可使用 `fb0a27032` 的源码。新助手的生产安装须独立执行 Ubuntu 实机验收。
- **安装与网页登录密码分离**：沙箱安装脚本不设置或存储网页登录密码。Host 可选密码见 §21。

### 统一子智能体的窄写根（2026-10-23）

`working_dir_write` 请求通过 `workspace_write_root` 指定窄写根。工作区父挂载只读，固定子工作目录挂载可写。助手与应用独立部署，需要更新时走原安装入口。遇到不兼容的助手应报告升级要求，禁止扩大可写范围或回退到 direct。
