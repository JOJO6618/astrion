# WSL2 等价强度沙箱可行性调研报告

调研日期：2026（本轮）。结论先行：**WSL2 可以做出与 macOS sandbox-exec 等价强度的沙箱**，核心路径是「专用 WSL2 发行版 + 发行版内 user/mount/network namespace（unshare 或 bubblewrap）」。因为 WSL2 跑的是真实 Linux 内核（如 6.6.x-microsoft-standard-WSL2），Linux 侧的方案（unshare / bwrap）基本可以完全复用；最大的工程成本在 Windows 侧的发行版供给、路径转换和 wsl.exe 的编码/开销问题。

---

## 1. 发行版供给：可行（编程式部署专用沙箱发行版）

### 结论
**可行**。不要依赖用户已有的发行版，而是用 `wsl --import` 导入一个官方 rootfs tar，创建专用 distro（如 `astrion-sandbox`）。这是官方支持的、完全非交互的路径。

### 具体做法

**rootfs 官方来源：**
- Ubuntu WSL 专用 rootfs：`https://cloud-images.ubuntu.com/wsl/<release>/current/*-wsl.rootfs.tar.gz`。Ubuntu 官方明确提供 tar-based WSL 发行格式用于「distribute, install, and manage Ubuntu WSL instances」（[Ubuntu 官方博客](https://ubuntu.com/blog/ubuntu-wsl-new-format-available)）。社区实践即用 `wsl --import` 导入这些 rootfs（[buildroot GettingStarted](https://github.com/TiMaMi-GmbH/buildroot-external-timami/blob/main/GettingStarted.md)、[Zenn 教程](https://zenn.dev/dozo/articles/a633794f6d7575)）。
- Alpine minirootfs（更轻量，~3MB）：`https://dl-cdn.alpinelinux.org/alpine/v3.xx/releases/x86_64/alpine-minirootfs-*-x86_64.tar.gz`，官方下载页提供 MINI ROOTFS，社区有完整 WSL2 导入实践（[tonym.us: Porting Alpine Linux RootFS to WSL2](https://tonym.us/porting-alpine-linux-rootfs-wsl2.html)、[PowerShell-Wsl-Alpine](https://github.com/antoinemartin/PowerShell-Wsl-Alpine)、[nathanchance: 从 LXC 镜像创建 WSL2 发行版](https://nathanchance.dev/posts/wsl2-distros-from-lxc-images/)）。
- 许可：Ubuntu/Alpine 均为自由软件，rootfs tarball 由官方公开发布供再分发与导入，无许可障碍。

**导入命令序列（官方文档，[Microsoft Learn — Basic commands for WSL](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)）：**
```powershell
wsl --import astrion-sandbox C:\Path\To\InstallDir ubuntu-wsl.rootfs.tar.gz --version 2
```
`--import` 完全非交互，导入的 distro 默认 root 登录，无首启用户创建向导（Store 版 distro 才有 OOBE）。注意：import 的 distro 没有 launcher exe，改默认用户要靠发行版内 `/etc/wsl.conf` 的 `[user] default=`。

**`wsl --install` 的交互问题：** Store 渠道安装的 distro 首启会进入交互式用户创建，且 `--install` 本身支持 `--no-launch` / `--no-distribution` 选项规避部分交互（[Microsoft Learn](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)，另见 [WSL issue #10386](https://github.com/microsoft/WSL/issues/10386) 讨论 `--no-launch` 后自行脚本化配置的做法）。但对沙箱场景，**推荐完全绕开 `--install`，用 `--import`**：可控、可重复、无 OOBE、与用户的其他发行版零耦合。

**检测并排除 docker-desktop 等不可用 distro：**
- 已安装发行版记录在注册表 `HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Lxss`（每个 GUID 子键含 `DistributionName`、`BasePath`、`Version` 等）（[Learning in the Open: Lxss 注册表结构](https://learningintheopen.org/tag/hkcusoftwaremicrosoftwindowscurrentversionlxss/)）。
- `docker-desktop` / `docker-desktop-data` 会出现在 `wsl -l -v` 里，但它们是 Docker Desktop 的内部基础设施 distro（[Super User 讨论](https://superuser.com/questions/1729811/why-are-these-multiple-wsl-distributions-on-windows)）。
- 程序化排除策略（建议多层）：① 名字黑名单（`docker-desktop*`）；② 试运行探测 `wsl -d <name> -e true`，失败即排除；③ 最稳妥：根本不枚举用户 distro，只认自己创建的 `astrion-sandbox`（先 `wsl -l -q` 检查是否已存在，存在则直接用或校验后重建）。注意 `wsl -l` 的输出是 UTF-16（见第 7 节）。

---

## 2. Windows 路径访问与 drvfs 语义：可行，但要注意 9P 元数据语义

### 结论
**可行**。`E:\path` → `/mnt/e/path` 是机械转换（盘符小写、反斜杠转正斜杠）。权限语义取决于 drvfs 挂载选项；WSL2 下 Windows 盘实际以 9p 协议挂载。

### 要点
- WSL2 中 Windows 盘的 mount 输出形如：`C:\ on /mnt/c type 9p (rw,...,aname=drvfs;path=C:;...)`（[WSL issue #4774](https://github.com/microsoft/WSL/issues/4774)）——WSL1 是 drvfs 内核驱动，WSL2 是 9P over virtio，语义基本对齐。
- `/etc/wsl.conf` 的 `[automount]` 可配 `options = "metadata,uid=...,gid=...,umask=...,fmask=...,dmask=...,case=off|dir|force"`（[Microsoft Learn — Advanced settings configuration in WSL](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)）。
- **关键坑：`metadata` 默认关闭**。未开 metadata 时，9p/drvfs 上 `chmod` 不生效、权限由 umask/fmask 统一伪造；开 metadata 后 chmod 结果会持久化到 NTFS 扩展属性（[Super User](https://superuser.com/questions/1323645/unable-to-change-file-permissions-on-ubuntu-bash-for-windows-10)、[akr.am](https://akr.am/blog/posts/fix-wsl-file-system-behavior)）。因此**不要用 chmod 作为 /mnt 下文件的只读控制手段**——权限语义不可靠。只读控制应走 mount 层（见第 3 节的 ro bind mount），那是 VFS 层强制，与底层 9p 无关。
- 另一坑：9p 不支持 inotify 跨 Windows 侧变更（Rolldown 文档明确说明 [Windows 侧修改文件 WSL2 内 watch 收不到事件](https://rolldown.rs/reference/inputoptions.watch)），与本任务关系不大但值得知道。
- 反向保护：如果希望沙箱内根本看不到其他 Windows 盘，可以在专用发行版的 `/etc/wsl.conf` 里设 `[automount] enabled=false`，再按需只挂载工作区所在盘；fstab 里也支持 ro 挂载（[tonym.us: Improve WSL Security with Read-Only Filesystem](https://tonym.us/improve-wsl-security-with-read-only-filesystem.html)）。

---

## 3. 文件只读与写范围（mount namespace / bubblewrap）：可行

### 结论
**可行，且是整套方案的核心**。WSL2 是真实 Linux 内核，user/mount/network namespace 均可用；`unshare --mount` + ro bind 链条成立；**bubblewrap 可以直接装进 WSL2 使用**，Linux 方案可完全复用。

### 证据
- OpenAI Codex CLI 的 Linux 沙箱就是 bubblewrap，其在 WSL2 上实际运行（[codex issue #22469](https://github.com/openai/codex/issues/22469)：环境明确为 `WSL2 (kernel 6.6.114.1-microsoft-standard-WSL2)`，问题只是 bwrap 的 `--dev /dev` 没暴露 `/dev/dxg` GPU 节点——说明 bwrap 沙箱本体在 WSL2 工作正常）。
- WSL2 内核支持 user namespace 与 network namespace：内核 6.6.87.2-microsoft-standard-WSL2 上 `unshare(CLONE_NEWUSER|CLONE_NEWNET)` 可用（[CVE-2026-43284 PoC 的实测环境记录](https://sploitus.com/exploit?id=9B3D53CA-E954-5110-8888-1957B931A61B)）；`unshare -Urm` + bind + remount ro 的完整链条在 codex issue #9254 中有逐命令验证（[链接](https://github.com/openai/codex/issues/9254)）。
- **WSL1 不可用**：WSL1 内核不支持 user namespace，`unshare -Ur` 直接 EINVAL，bwrap 报 "kernel does not support user namespaces"（[codex issue #16076](https://github.com/openai/codex/issues/16076)）。另外 WSL1 有 ro bind mount 在有可写 fd 时 EBUSY 的 bug（[WSL issue #3549](https://github.com/microsoft/WSL/issues/3549)）。**方案必须强制 WSL2**（导入时 `--version 2`，启动前 `wsl -l -v` 校验）。
- 已知小限制：`unshare --time` 在 WSL 上不支持（[WSL issue #9223](https://github.com/microsoft/WSL/issues/9223)），与本需求无关。

### 推荐命令链（发行版内，root 或 userns 均可）
方案 A（纯 unshare，无需装包）：
```bash
wsl -d astrion-sandbox -u root -- bash -c '
  unshare --mount --propagation private bash -c "
    mount --make-rprivate /                       # 关键：先切断 shared 传播，避免污染宿主/其他会话
    mount --bind / /                              # 如需整树 ro：先 bind 再 remount,ro（bind+ro 一步到位有内核老 bug，必须两步）
    mount -o remount,bind,ro /
    mount --bind /mnt/e/workspace /mnt/e/workspace
    mount -o remount,bind,rw /mnt/e/workspace     # 工作区 rw 放回去
    mount --bind /tmp /tmp && mount -o remount,bind,rw /tmp
    exec sudo -u sandboxuser bash                 # 降权后 exec 目标 shell
  "
'
```
方案 B（bubblewrap，推荐，与 macOS/Linux 共用同一套策略描述）：
```bash
bwrap \
  --ro-bind / / \
  --bind /mnt/e/workspace /mnt/e/workspace \
  --bind /tmp /tmp \
  --unshare-net \
  --die-with-parent \
  -- bash
```
bwrap 的 `--unshare-net` 会在新 netns 里创建 loopback（[fromager issue #472](https://github.com/python-wheel-build/fromager/issues/472)），天然实现「禁网但保留 lo」。

### 两个必须注意的工程细节
1. **mount 传播**：WSL2 的挂载默认是 shared propagation。在新 mount namespace 里 remount ro 之前如果不 `mount --make-rprivate /`，ro 事件可能传播回初始 namespace，影响同 VM 里的其他会话/发行版。先私有化再操作是标准做法。
2. **bind ro 要两步**：`mount --bind` 本身不继承 ro 选项，必须 `mount -o remount,bind,ro`（经典内核行为，[Unix.SE](https://unix.stackexchange.com/questions/128336/why-doesnt-mount-respect-the-read-only-option-for-bind-mounts)、[LWN: Read-only bind mounts](https://lwn.net/Articles/281157/)）。
3. 只读是 VFS 层强制，9p 后端无法绕过（进程在 Linux 侧的一切写都被 VFS 挡掉）；但**从 Windows 侧的直接写入不受此约束**——沙箱只防 WSL 内的不可信进程。

---

## 4. 网络隔离：可行（三档都能实现）

### 结论
**可行**。三档映射：
- **禁网**：`unshare --net`（新 netns 无任何外部接口）或 `bwrap --unshare-net`；
- **仅回环**：`unshare --net` 后 `ip link set lo up`（在新 ns 内有 CAP_NET_ADMIN，可用），或直接用 bwrap（自动带 lo）；
- **全开**：不加 net namespace。

### 依据与细节
- `unshare -rn` 创建无外部连通性的 netns，是构建工具的常用隔离手段（[fromager issue #472](https://github.com/python-wheel-build/fromager/issues/472)）；进程隔离教学也覆盖 `ip netns`/`unshare` 路径（[oneuptime 教程](https://oneuptime.com/blog/post/2026-03-20-isolate-process-network-namespace/view)）。
- 新 netns 里 lo 存在但初始 DOWN，需要 `ip link set lo up`；bwrap 较新版本自动完成这步。个别环境 bwrap 的 loopback 配置会失败（RTM_NEWADDR EPERM，[codex issue #15982](https://github.com/openai/codex/issues/15982)）——若在目标环境复现，降级为 `unshare --net` + 手动 `ip link set lo up`。
- 发行版级 iptables/nftables 也可行（WSL2 是完整内核，实例内 root 有完整 netfilter 能力），但 netns 方案更彻底（连路由表都不存在，无法绕过），优先推荐。
- **localhostForwarding 的影响**：默认 `localhostForwarding=true`（[Microsoft Learn](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)），Windows 侧可通过 localhost 访问 WSL 内监听的端口。对隔离的含义：① 沙箱内进程监听端口可能被 Windows 宿主触达（本地跨进程攻击面）；② NAT 模式下 WSL 内可经宿主网关 IP 访问 Windows 上的 localhost 服务（如代理 7890 端口）。**`unshare --net` 把这两条路一起断掉**（新 ns 里根本没有到宿主的链路），所以 netns 方案天然覆盖该问题；若走 iptables 方案则必须额外阻断到宿主网关 IP 的流量。
- 另可在 `.wslconfig` 设 `networkingMode=none` 做 VM 级断网，但它是全局开关、影响所有发行版，不适合按需沙箱。

---

## 5. 交互式 shell（持久会话）：可行

### 结论
**可行**。namespace 隔离是进程树属性，把沙箱包装器作为长驻 shell 的父进程即可，对一次性命令和持久终端同样成立。

### 做法
- 一次性命令：`wsl -d astrion-sandbox -u root -- bwrap <flags> -- bash -lc "cd ... && cmd"`。
- 持久交互终端：把同样的包装器包在交互 shell 外面——`wsl -d astrion-sandbox -- bwrap <flags> -- bash`（或先 `unshare` 进 namespace 再 exec bash）。bwrap 是 exec 语义，整个会话生命周期都活在沙箱里；加 `--die-with-parent` 保证 wsl.exe 断开时沙箱内进程树随之回收。
- 会话内多条命令复用同一沙箱：沙箱内起 tmux 或直接复用该 bash 的 stdin/stdout 管道，host 侧通过持有 wsl.exe 进程的 stdio 持续下发命令。
- 若要「先配置一次隔离、后续多次进入」，也可以 `unshare --mount --net --fork` 后用 `nsenter -t <pid> -m -n` 反复进入同一组 namespace（nsenter 是标准 Linux 能力，WSL2 可用）。

---

## 6. 性能：有限（每次起 wsl.exe 有数百 ms 开销，必须做进程复用）

### 结论
**可行但需要工程优化**。实测/社区数据一致指向：wsl.exe 每次调用有数百毫秒级固定开销，高频命令场景必须保活 + 复用长驻进程。

### 数据点
- 从 Windows 侧经 wsl.exe 调用 Linux 程序，单次约 500–800ms（[Super User #1663063](https://superuser.com/questions/1663063/slow-bash-under-wsl)）。
- 极端病态案例：wsl.exe 会话内命令 >5s 而 SSH 进同一 WSL <0.1s（[WSL issue #4712](https://github.com/microsoft/WSL/issues/4712)），说明 wsl.exe 通道本身可能是瓶颈。
- 第三方 cold boot 基准：基于 WSL2 的 agent 运行时冷启动约 3500ms（[ZeroClaw 对比基准](https://skywork.ai/skypage/en/openclaw-windows-compatibility-guide/2048651699598520321)，社区数据，仅供参考量级）。
- 对照：Linux 原生 bwrap/容器级沙箱开销约 300ms 以内（[Julia Evans 的 benchmark](https://jvns.ca/blog/2022/06/28/some-notes-on-bubblewrap/)），即大部分开销在 wsl.exe 桥接层而非沙箱本身。

### 优化手段（都已验证可行）
1. **防 VM 休眠**：`.wslconfig` 里 `vmIdleTimeout=-1`（默认 60000ms 空闲即关 VM，[Microsoft Learn](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)）；新版 WSL 还有 `[general] instanceIdleTimeout`（默认 15000ms，[GreenGorych .wslconfig 参考](https://greengorych.io/blog/complete-wslconfig-reference-and-template/?q=)）。
2. **保活**：后台跑一个无害长驻命令，如 `wsl --exec dbus-launch true`（[知乎实践](https://www.zhihu.com/question/662699218/answer/3578847718)）。
3. **复用**：host 侧持有一条长驻的 `wsl -d astrion-sandbox -- bwrap ... bash` 进程，通过其 stdin/stdout 反复下发命令（推荐）；或沙箱内起一个小的命令服务（FIFO/socket），后续命令经 wsl.exe -e 投递。

---

## 7. 编码与输出污染：有限（有开关，但需逐项处理）

### UTF-16 输出
- **结论：可控。** wsl.exe 自身输出（如 `--list`）是 UTF-16LE 无 BOM，不尊重系统代码页（[WSL issue #4607](https://github.com/microsoft/WSL/issues/4607)）。
- **解法：设置环境变量 `WSL_UTF8=1`**，wsl.exe 即改用 UTF-8 输出（微软官方支持的开关；VS Code 曾因此产生解析 bug，反向证实了该行为，[vscode issue #276253](https://github.com/microsoft/vscode/issues/276253)）。host 侧启动 wsl.exe 子进程时统一注入 `WSL_UTF8=1` 即可。

### localhost 代理警告
- **结论：可抑制，但最佳路径建议实测确认。** 警告 `wsl: A localhost proxy configuration was detected but not mirrored into WSL. WSL in NAT mode does not support localhost proxies.` 由 wsl.exe 在每次启动时打印（[WSL issue #13456](https://github.com/microsoft/WSL/issues/13456)、[MathWorks 论坛](https://fr.mathworks.com/matlabcentral/answers/2174804-wsl-proxy-issue-affecting-compiler-package-microservicedockerimage-in-matlab)、[kevinskii.dev](https://kevinskii.dev/posts/wsl-networking/)）。触发条件是 `autoProxy=true`（默认）+ Windows 配了监听 127.0.0.1 的代理 + NAT 模式。
- 抑制手段：
  1. `.wslconfig` 设 `[wsl2] autoProxy=false`（官方文档确认该键控制「Enforces WSL to use Windows' HTTP proxy information」，[Microsoft Learn](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)；社区模板即推荐关代理时设 false，[feamcor gist](https://gist.github.com/feamcor/46687e86513c106c438bfa2715249609)）。逻辑上检测关闭则警告不再产生——**建议落地前实测一轮**，因为微软没有单独文档承诺「该警告由 autoProxy 开关控制」。
  2. 或切 `networkingMode=mirrored`（镜像模式下 localhost 代理天然可用，警告消失；但会改变整体网络行为，并存在与代理工具冲突后回退 NAT 的已知问题，[issue #13456](https://github.com/microsoft/WSL/issues/13456)、[issue #10794](https://github.com/microsoft/WSL/issues/10794)）。
  3. 兜底：该警告走 stderr，可在 host 侧对 wsl.exe 的 stderr 做已知模式过滤（不影响子命令的退出码和 stdout）。
- `.wslconfig` 修改后需 `wsl --shutdown` 重启 VM 生效（8 秒规则，[Microsoft Learn](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)）。WSLENV 是环境变量透传机制（仅传白名单变量，[Zenn 实践](https://zenn.dev/kwrkb/articles/d927a0eac72d3d?locale=en)），与该警告无关。

---

## 总体评估矩阵

| 能力 | 结论 | 关键手段 |
|---|---|---|
| 专用沙箱发行版供给 | 可行 | `wsl --import` + Ubuntu cloud WSL rootfs / Alpine minirootfs；注册表 Lxss + 名字黑名单 + 试运行探测排除 docker-desktop；推荐只认自建 distro |
| Windows 路径访问 | 可行 | 盘符机械转换；权限控制走 mount 层而非 chmod（9p metadata 默认关） |
| 只读/写范围（mount ns） | 可行 | `unshare --mount` + `make-rprivate` + 两步 ro bind + 工作区 rw 放回；或 bubblewrap；必须 WSL2（WSL1 无 userns） |
| 网络三档 | 可行 | `bwrap --unshare-net` / `unshare --net` + `ip link set lo up`；localhostForwarding 风险被 netns 天然覆盖 |
| 持久交互 shell | 可行 | 沙箱包装器 exec 长驻 bash；或 nsenter 重入 |
| 性能 | 有限 | wsl.exe 单次数百 ms；必须 VM 保活 + 长驻进程复用 |
| 编码/输出污染 | 有限 | `WSL_UTF8=1` 解决编码；`autoProxy=false` 抑制代理警告（建议实测）；stderr 模式过滤兜底 |

**落地建议优先级**：专用 distro（--import Alpine/Ubuntu rootfs）→ 发行版内 bubblewrap 统一沙箱（ro-bind / + 工作区 bind + --unshare-net 三档）→ 长驻 bwrap bash 会话复用 → `WSL_UTF8=1` + `autoProxy=false`。这样 Linux 与 Windows(WSL2) 可共用几乎同一份沙箱策略代码。
