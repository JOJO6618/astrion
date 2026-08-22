# WSL2 + bwrap 沙箱概念验证（PoC）实验报告

> 日期：2026-07-30 · 实验机：Windows 11 (26200) · WSL2 内核 5.15.167.4
> 结论先行：**路线可行，隔离强度达到调研预期，可以进入源码改造。**
> 前提条件：必须使用专用发行版并关闭 Windows 互操作（interop），否则存在沙箱逃逸（见 E8）。

## 1. 实验环境搭建（已验证的部署流程）

```cmd
:: 1. 下载 Alpine minirootfs（~3.5MB）
curl -L -o alpine-minirootfs.tar.gz https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/x86_64/alpine-minirootfs-3.21.3-x86_64.tar.gz
:: 2. 导入为专用沙箱发行版（非交互，可编程）
wsl --import astrion-sandbox <安装目录> alpine-minirootfs.tar.gz --version 2
:: 3. 发行版内配置（详见 §3 的发现）
::    - /etc/wsl.conf: [interop] enabled=false / appendWindowsPath=false；[network] generateResolvConf=false
::    - /etc/resolv.conf: 固定公共 DNS（223.5.5.5 / 119.29.29.29）
::    - /etc/apk/repositories: 换国内镜像（mirrors.aliyun.com）
::    - apk add bubblewrap bash ncurses-libs python3 git
```

## 2. 实验结果总表（24 项，23 PASS / 1 INFO）

沙箱命令模板（与项目现有 `_build_linux_common_plan` 同构）：

```
只读模式:   bwrap --die-with-parent --new-session --unshare-all --share-net \
            --ro-bind / / --ro-bind <ws> <ws> --chdir <ws> \
            --proc /proc --dev /dev --tmpfs /tmp -- bash -lc <cmd>
工作区可写: 同上，工作区改为 --bind <ws> <ws>
网络全禁/仅回环: 去掉 --share-net（--unshare-all 自带的 unshare-net，lo 自动可用）
```

| 分组 | 实验 | 结果 | 说明 |
|---|---|---|---|
| E0 基础 | 发行版执行 / bwrap 0.11 / user namespace | ✅ | WSL2 满足 bwrap 全部前提 |
| E0 路径 | `E:\...` → `/mnt/e/...` 转换 + 工作目录 | ✅ | 机械转换即可，含空格路径正常 |
| E0 编码 | `WSL_UTF8=1` 解决 wsl.exe UTF-16 乱码 | ✅ | 必须设置 |
| E0 输出 | localhost 代理警告仅在 stderr | ✅ | 可在执行器层过滤 |
| E1 只读 | 工作区内写入 → `Read-only file system` | ✅ | 报错关键词与 mac 语义一致，审批关键词匹配可直接复用 |
| E1 只读 | 系统/工作区读取正常 | ✅ | |
| E1 只读 | 写 Linux 系统目录被拒 | ✅ | |
| E1 只读 | **写工作区外 Windows 盘（9P 子挂载）被拒** | ✅ | 关键项：ro-bind / 对 9P 挂载同样生效 |
| E2 可写 | 工作区内写入真实落盘 Windows | ✅ | |
| E2 可写 | 工作区外 Windows 盘写入被拒 | ✅ | |
| E2 可写 | Linux 系统目录写入被拒；tmpfs /tmp 可写 | ✅ | 对齐 macOS /tmp 语义 |
| E3 禁读 | 未掩蔽时敏感文件可读（风险确认） | ✅ | 前置确认 |
| E3 禁读 | `--ro-bind /dev/null <file>` 掩蔽单文件 | ✅ | |
| E3 禁读 | `--tmpfs <dir>` 掩蔽整目录 | ✅ | 可实现 macOS deny-read 等价 |
| E4 网络 | share-net 外网可达 | ✅ | |
| E4 网络 | unshare-net 外网不可达 | ✅ | |
| E4 网络 | unshare-net 下 lo 可用、127.0.0.1 实际通信 | ✅ | 等价 macOS restricted（仅回环）语义 |
| E5 综合 | 只读模式 `rm -rf` 工作区被拦截 | ✅ | |
| E5 综合 | 可写沙箱 `rm -rf /mnt/c/Windows/...` 全部 Read-only | ✅ | |
| E6 交互 | bwrap 内 `bash -i` stdio 逐条往返 | ✅ | 持久终端方案成立 |
| E7 性能 | 单次 wsl.exe 调用 ~950-1000ms | ℹ️ | 后续需长驻进程复用优化 |
| E8 工具链 | git init/add/commit 全流程、python3 写文件 | ✅ | 工作区内正常，工作区外 OSError 拒绝 |

## 3. 关键发现（实验中新暴露、调研未覆盖的）

### E8-逃逸（最严重）：必须关闭 Windows interop
- **未关闭时**：只读 bwrap 沙箱内执行 `cmd.exe /c echo pwned > E:\...\file.txt` **成功写入 Windows 盘**。interop 进程由 WSL 会话 init 在命名空间外拉起，bwrap 完全管不到 → 沙箱形同虚设。
- **封堵方式**（已验证）：专用发行版 `/etc/wsl.conf` 设 `[interop] enabled=false` + `appendWindowsPath=false`，重启发行版后 `cmd.exe: command not found`，逃逸封死。
- 结论：沙箱发行版必须是**专用、受控配置**的，绝不能复用用户自己的 Ubuntu 等发行版。Claude Code 的 WSL 沙箱同样禁 interop，属行业共识做法。

### 网络环境适配（本机实测暴露）
- 宿主使用 localhost 代理时，WSL NAT 模式的 DNS 转发（10.255.255.254 → 宿主解析器）**会失效**（时好时坏）；直连公共 DNS（223.5.5.5）正常 → 发行版需固化 `generateResolvConf=false` + 公共 DNS。
- apk 官方源大包下载会卡死，换阿里云镜像后秒装 → 发行版应内置国内镜像（或做成可配置）。
- wsl.exe 每次启动都在 stderr 打 localhost 代理警告 → 执行器需过滤 stderr 该行（或在 `.wslconfig` 全局设 `autoProxy=false`，影响面大，不推荐默认做）。

### 工程细节
- Git Bash 调用 wsl.exe 会被 MSYS2 路径转换毁掉 Unix 参数（`/etc/...` → `E:/KimiData/...`）→ 仅影响开发调试（设 `MSYS2_ARG_CONV_EXCL='*'`），Python subprocess 不受影响。
- `bash` 需显式安装（Alpine 默认 busybox sh），且需 `ncurses-libs` 才能跑。

## 4. 与 macOS 沙箱的能力对照（最终形态）

| 能力 | macOS（现状） | Windows WSL2+bwrap（PoC 验证后） |
|---|---|---|
| 只读模式 | sandbox-exec deny-write | bwrap 全 ro-bind ✅ |
| 工作区可写 | profile writable paths | ro-bind / + bind ws ✅ |
| 敏感路径禁读 | deny subpath / regex | tmpfs/dev-null 掩蔽（无 regex，需逐个路径）⚠️ |
| 网络三档 | profile 网络规则 | share-net / unshare-net（restricted≈仅回环）✅ |
| 审批链语境 | Unix 报错关键词 | 完全一致（Read-only file system / Permission denied）✅ |
| 只读命令白名单 | Unix 命令集 | 完全一致（同为 Linux bash）✅ |
| 交互终端 | sandbox-exec bash -i | bwrap bash -i ✅ |

遗留限制（二期）：单次调用 ~950ms 开销需长驻 WSL 进程复用；禁读不支持 regex（可改为启动前枚举 .env 类文件逐个掩蔽）；seccomp 过滤器暂未接入（bwrap 本身已满足 FS/网络隔离需求）。

## 5. 原始实验日志

完整 24 项原始输出见 `.wsl-poc/results.md`（含 E5.2 逐文件 Read-only 报错洪流，可佐证 9P 挂载保护的真实性）。实验脚本：`.wsl-poc/run_experiments.sh`。
