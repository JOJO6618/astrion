# Linux host 沙箱（完整）

> 本文由 `AGENTS.md` §20 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 20) Linux host 沙箱（2026-10-08 首批适配）

- **独立安装入口**：`sudo bash scripts/setup-linux-sandbox.sh`，默认授权 `SUDO_USER`；root 直接安装必须加 `--user <普通账户>`。支持 `--dry-run`、`--status`、`--no-install-dependencies`。不依赖 Astrion CLI，也不启动后端。底层 `modules/linux_sandbox/setup.py` 可独立调用。
- **首批环境**：Ubuntu 24.04、kernel 6.8、systemd 255、cgroup v2、AppArmor、libseccomp；其它发行版不自动放行。安装器可装系统依赖，但不得通过关闭 AppArmor/userns 限制或降级 direct 来绕过失败。安装器全局安装与网页 sudo/polkit 仍需实机验收。
- **可信助手**：`/usr/local/libexec/astrion-sandbox` + `/etc/astrion-sandbox/helper.json` + `astrion-sandbox.service`。代码、配置、C 身份入口和 BPF 必须 root-owned、不可由普通用户修改。运行时不从用户源码树导入 root Python 模块。成功升级保留旧助手备份，验收失败回滚旧文件及服务状态。
- **调用与身份**：普通客户端通过固定 SEQPACKET socket 传严格 JSON 和三个 stdio FD；SO_PEERCRED/SO_PEERSEC/pidfd 核验身份，拒绝 UID0 和已在沙箱标签下的递归调用。UID、groups、助手路径和 unit 属性不能来自模型参数。特权阶段仅挂载，任意用户命令只能在 C 入口降到真实普通 UID/GID、清能力和开启 NNP 后执行。
- **文件根**：系统目录和精确配置清单唯一来源为 `modules/linux_sandbox/constants.py`，主/子/只读/后台/持久终端统一走 `plans.py`。源在普通用户 fork 中逐组件 no-follow 打开并固定 FD；cwd 在降权后再次验证固定 inode。`/proc`、`/dev`、`/sys` 和内部 runtime 禁止作为宿主挂载授权。匿名 shell `/tmp`、HOME 不授权原生文件工具访问对应宿主路径；额外授权每次读取现行工作区政策，最低子档不得额外持久写入。
- **网络与回收**：共享 host 网络；每任务独立 transient unit，restricted=deny any+allow localhost，none=deny any。runner 检查 enforcing 标签和实际 ingress/egress BPF，缺失则拒绝。受限档拒绝抽象 Unix socket；none 通过独立 seccomp 禁止新 Unix socket 和 datagram socketpair，保留匿名 stream/seqpacket IPC。客户端断开/pidfd 退出、助手停止和取消均回收本任务 unit；启动前二次握手与可撤销 lease 防迟到执行。安装后 unit 绑定 broker 服务生命周期。
- **网页/headless**：沿用现有沙箱向导及三条 `/api/sandbox/*` 路由，Linux manager 与独立脚本共用安装实现；无系统授权方式时给终端命令。网页不收 sudo 密码。三端点共享现有 host Cookie/Bearer 装饰器，沙箱适配阶段未改变认证逻辑。
- **证据与限制**：`test/2026-10-08_Linux_host沙箱实验/`（248 项网络/文件断言和双向 localhost）；`test/2026-10-08_Linux_host沙箱助手/`（真实 UID、完整 broker、嵌套拒绝、终端管道、SIGKILL 孙进程清理）；`test/2026-10-08_Linux_host沙箱适配/`（契约、安装回滚、Linux 专属回归和 dry-run）。这些是有限验证，不等同跨发行版安全认证或生产全局安装验收。
- **安装与网页登录密码分离**：沙箱安装脚本不设置或存储网页登录密码。安装说明见 `docs/linux_host_sandbox_setup.md`；Host 可选密码见 §21。
