# Linux host 沙箱真机实验报告

日期：2026-10-08。状态：独立 PoC 已验证；未修改 Astrion 正式沙箱实现，不能据此宣称产品已经支持 Linux host 沙箱。

## 结论

文件隔离可以沿用 Windows WSL 沙箱的最小根文件系统结构。要同时保持宿主机 localhost 的双向访问，需要补充 Linux 专用的网络控制。

本机实测可行组合：bubblewrap 最小文件系统 + 共享宿主网络 + cgroup eBPF IP 过滤 + seccomp + 清除 capabilities。受限网络只允许 127.0.0.0/8 和 ::1；通过 systemd 临时单元附加过滤。

共享网络也会共享抽象 Unix socket 的命名空间。仅有 IP 过滤不能限制这些端点。额外使用 cgroup UNIX_CONNECT / UNIX_SENDMSG 钩子可阻断 Unix socket 主动连接及发送，同时保留 socketpair。但本实验的钩子同时阻断路径形式和抽象形式的 Unix socket，存在需要用户明确选择的兼容性取舍。

## 环境与范围

- SSH 服务器：59.110.19.30；登录身份为 root。
- 系统：Ubuntu 24.04.2 LTS，x86_64，Linux 6.8.0-40-generic。
- Python：3.12.3；systemd：255；cgroup v2；libseccomp.so.2。
- bubblewrap：0.9.0，由 Ubuntu apt 仓库下载 deb，在实验目录内解压执行；没有系统安装。
- 远程目录：`/tmp/astrion-linux-sandbox-20261008-2Bjdqo/`；第二批位于 `batch_02/`。
- 本地脚本与原始证据：本 README 所在目录。
- 最终远程目录占用约 628 KiB，保留用于复核。顶层目录权限已恢复为 0700。

文件写入、删除、chmod、符号链接、Unix socket 和包解压均针对实验目录内的自建对象。匿名 `/tmp` 写入仅存在于沙箱自己的 tmpfs。没有改动部署代码、运行时数据、全局防火墙、sysctl、AppArmor 配置，也没有安装系统软件或重启业务服务。

目录外的运行态操作是创建专属 systemd 临时单元及其 cgroup/BPF 过滤器。单元名均以 `astrion-sandbox-` 开头，仅承载实验子进程，设置运行时间及资源限制，并在任务结束后回收。

## 实测结果

第一批：7 种组合，189/189 项预期断言通过，见 `report.json`。其中 1.1.1.1:443 在无过滤基线也超时，不能作为公网阻断证据。

第二批：8 种组合，248/248 项预期断言通过，见 `report_v2.json`。公网对照改用预先验证可连接的 example.com（当时 IPv4 104.20.23.154:80），仅进行 TCP 握手，没有发送应用数据。开放网络时各组均可连接，受限时被阻断。

反向 localhost：4/4 个预期对照通过，见 `reverse.json`。这些数字是有限探针的断言计数，不代表全面安全认证。

| 行为 | 共享网络，无 IP 过滤 | 共享网络，仅 localhost | localhost + Unix 钩子 | 独立网络命名空间 |
|---|---|---|---|---|
| 访问宿主 127.0.0.1，TCP/UDP | 成功 | 成功 | 成功 | 失败 |
| 访问宿主 ::1，TCP/UDP | 成功 | 成功 | 成功 | 失败 |
| 访问宿主非回环测试地址，TCP/UDP | 成功 | 阻断 | 阻断 | 失败 |
| 公网 TCP 握手 | 成功 | 阻断 | 阻断 | 无路由 |
| 宿主访问沙箱启动的 127.0.0.1 服务 | 成功 | 成功 | 成功 | 失败 |
| 连接宿主抽象 Unix socket | 成功 | 成功 | EPERM | 失败 |
| 连接已挂入工作区的路径 Unix socket | 成功 | 成功 | EPERM | 成功 |
| socketpair 进程内通信 | 成功 | 成功 | 成功 | 成功 |

独立网络命名空间不会隔离已经挂入文件系统的路径 Unix socket。本实验观察到，即使没有 IP 网络，路径 Unix socket 仍可通信，因此不能把 `--unshare-net` 等同于“完全禁止所有本地通信”。

### 文件、进程和开发工具

- 可写工作区允许创建、chmod 和删除自建文件。
- 只读工作区中上述写操作返回 EROFS；文件内容仍可读。
- 仅可读授权目录能读，不能写。
- 可读可写授权目录在可写组合中能读写；只读组合仍只读。
- 未挂入的实验兄弟目录不能读取或写入；指向它的工作区符号链接也不能读取。
- 所有组合下实验兄弟目录的普通文件哈希保持不变。
- 真实数据目录 `/opt/agent/runtime`、宿主 `/root/.ssh`、cgroup 文件系统在沙箱里不可见；仅检查可见性，没有读取这些目录内容。
- `/proc` 仅出现沙箱 PID 命名空间中的进程。
- 实验 BPF 策略包含 52 条规则、656 字节；阻断嵌套网络命名空间和 Netlink socket 探针。
- Python 子进程、Python 线程和 git --version 正常。
- 额外读取沙箱自身 `/proc/self/status` 确认 CapPrm/CapEff/CapBnd 全为 0，NoNewPrivs=1，Seccomp=2；见 `final_audit.json`。

以上没有覆盖 Node/npm、venv 完整创建、真实 Astrion 原生文件工具、真实审批流、后台任务和持久终端集成，也没有覆盖所有绕过方式。

## 普通用户部署约束

见 `ordinary_user.json` 与 `apparmor.txt`：

- `kernel.unprivileged_userns_clone=1`。
- `kernel.apparmor_restrict_unprivileged_userns=1`。
- 切换为 uid/gid 65534 后，unshare 的 uid_map 写入失败，目录内 bwrap 的回环配置报 EPERM。
- 内核日志明确显示进入 `unprivileged_userns` profile，并拒绝 bwrap 的 setpcap/net_admin 能力。
- `kernel.unprivileged_bpf_disabled=2`。

因此，这轮验证的是 root 创建实验隔离和网络过滤，再由 bwrap 清除沙箱进程能力的路径；未验证普通用户可以开箱即用。

正式方案建议让 Astrion 本体继续以普通用户运行，安装阶段由管理员配置需要的 bwrap/AppArmor 能力及受限启动助手。助手负责创建指定任务 cgroup、附加过滤、核对可信工作区和授权路径、切换到调用者身份并启动沙箱。助手的权限接口、路径竞争防护、凭证传递、cgroup 逃逸防护和取消清理都需要单独设计与验证。不要把模型提供的任意命令直接交给通用 sudo 接口，也不要为兼容而全局关闭 AppArmor 或放开 BPF。

这是基于实验提出的设计建议，尚未实现，也尚未获得正式接入授权。

## 清理核验

`final_audit.json` 显示：

- 实验 systemd 单元列表为空。
- 匹配实验目录的 Python/bwrap/Unix 钩子进程为空。
- 实验 `astrion_unix` BPF 程序为空。
- bubblewrap 系统软件包仍未安装。

测试服务线程均已退出。远程只保留约 628 KiB 的脚本、包、二进制、模拟文件和结果。自动回收后再次 stop 可能返回“单元不存在”；最终 inactive 状态及上述核验是清理依据。

## 文件说明与复核

- `run_experiment.py`：八种组合的编排、夹具服务、断言和清理。
- `probe.py`：沙箱内文件/网络/进程探针。
- `seccomp_policy.py`：在实验目录生成 BPF 文件。
- `unit_helper.py`：只打开实验 BPF，记录当前实验 cgroup 钩子并 exec bwrap。
- `unix_gate.c`：仅向当前专属实验单元附加 Unix socket 拒绝钩子，不 pin BPF。
- `reverse_localhost.py`：宿主访问沙箱 localhost 的四种对照。
- `*.service.json`：实际附加的内核 BPF hook 清单。

脚本有意使用全新的实验目录以及 O_EXCL 创建策略文件；不应在原目录直接重复运行并覆盖第一轮证据。复核时在同一实验根下新建一批目录，拷入脚本并提供 tools/usr/bin/bwrap，目录内编译 `cc -Wall -Wextra -O2 unix_gate.c -o unix_gate`，再运行 `python3 run_experiment.py` 和 `python3 reverse_localhost.py`。

## 待讨论的产品决定

1. 是否接受 Linux 安装时由管理员配置一个范围受限的启动助手，使 Astrion 日常仍以普通用户运行？
2. 受限网络是否禁止主动连接 Unix socket？当前 PoC 保留 localhost TCP/UDP 和 socketpair，但会影响 Docker socket、Unix socket 数据库连接等用法。若要更细的允许规则，需要进一步 PoC。
3. 首批支持是否限定为已验证能力组合（例如 Ubuntu 24.04、内核 6.8、systemd/cgroup v2），其他环境明确报不支持而不自动放宽隔离？

后续正式适配需统一主智能体、固定子权限、只读/审批/可写、全局/工作区授权、前后台命令、持久终端及原生文件 IO；网络限制与审批必须保持独立，审批只能改变本次写权限。
