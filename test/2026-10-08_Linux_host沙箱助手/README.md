# Linux host 受限助手实验（2026-10-08）

测试机 Ubuntu 24.04.2、x86_64、kernel 6.8.0-40-generic、systemd 255、cgroup v2。SSH 为 root，沙箱命令真实身份为 UID/GID65534，不通过 namespace 显示假 UID。

## 结果

- `identity_results.json`：普通身份的 full/restricted/readonly 三组合，通过真实 UID、能力清零、root-private 文件读取/改权限拒绝、路径 Unix socket、抽象 socket、Node worker/npm/Git/Python venv 验证。对应远程 batch_06。
- `broker_results.json`：完整 root broker + 普通客户端的首次通过记录，对应 batch_11。
- `broker_results_v2.json`：加入递归请求拒绝、管道交互终端和执行前二次握手，对应 batch_12。
- `broker_results_v3.json`：最终代码版本，补私有 HOME 写入与所有可写档的 venv 创建，四组合全部命令成功，74/74 证据断言通过。对应 batch_13。
- `final_audit.json`：实验 unit、临时 profile、实验 BPF、控制 socket、任务目录均无残留；系统 bubblewrap 未安装；实验根权限恢复 0700。

复核已保存证据，不连接服务器：

```bash
python3 -B test/2026-10-08_Linux_host沙箱助手/verify_evidence.py
```

## 关键证据

| 项目 | full | restricted | readonly/restricted | none |
|---|---|---|---|---|
| 实际 UID/GID | 65534 | 65534 | 65534 | 65534 |
| CapPrm/CapEff/CapBnd | 全 0 | 全 0 | 全 0 | 全 0 |
| NoNewPrivs / Seccomp | 1 / 2 | 1 / 2 | 1 / 2 | 1 / 2 |
| 工作区读写 | 可写 | 可写 | 写入 EROFS | 可写 |
| 私有 HOME | 可写，文件属 UID65534 | 同左 | 同左 | 同左 |
| 未授权兄弟目录/越界链接 | 不存在 | 不存在 | 不存在 | 不存在 |
| 工作区 root-private 文件 | EACCES | EACCES | EACCES | EACCES |
| 额外只读授权 | 可读 | 可读 | 可读 | 可读 |
| 宿主 localhost | 可用 | 可用 | 可用 | 阻断 |
| 工作区路径 Unix socket | 可用 | 可用 | 可用 | 阻断 |
| 抽象 Unix socket | 可用 | EACCES | EACCES | 创建 EPERM |
| 内部 stream socketpair | 可用 | 可用 | 可用 | 可用 |
| Node worker/npm/Git | 返回 0 | 返回 0 | 返回 0 | 返回 0 |
| 创建并运行 Python venv | 返回 0 | 返回 0 | 不在只读档创建 | 返回 0 |

单独验证：即使将控制 socket 显式只读挂入，沙箱标签下的进程也不能创建新沙箱；普通客户端 SIGKILL 后，专属 cgroup 内另起会话的孙进程被回收。管道终端执行 echo/exit 正常；bash 的“无 job control”提示如实保留，测试没有将管道模式当作真实 PTY 验收。

## 实验范围与服务器数据保护

远程唯一实验根：`/tmp/astrion-linux-sandbox-20261008-2Bjdqo/`。工具包下载、编译、夹具文件、助手与报告均在根内。只创建临时 systemd 单元及专属 AppArmor 标签；结束后卸载自己的标签、停止自己的单元。没有修改 `/opt/agent/agents`、`/opt/agent/runtime`，没有重启现有业务服务，没有 apt 安装或改全局防火墙/sysctl。

早期 batch_07–10 的失败保留在远端，原因依次为 AppArmor 不支持用 addr 规则匹配路径 Unix socket、Python getsockopt 缓冲上限、长实验路径超过 Unix socket 长度限制。正式实现改用独立 none seccomp、1024 字节安全标签读取和短任务 rendezvous 名。没有关闭系统保护或采用 direct 回退。

普通 UID 的最初 unshare/bwrap 失败证据在前一阶段报告中，原因是本机 AppArmor 对非特权 userns 的限制。正式方案由管理员助手准备 namespace，再以真实普通 UID 执行。

完整网络 IP/TCP/UDP/IPv6 及双向 localhost 对照来自 `../2026-10-08_Linux_host沙箱实验/` 的 248 项矩阵与 4 项反向测试。最新 broker 验证使用同一 systemd IP 属性，并在运行前检查实际 ingress/egress BPF。

这些是有限隔离实验和回归，不等同生产全局安装、其它发行版、ARM 或所有攻击面的安全认证。正式安装脚本只完成 dry-run 和模拟回滚测试，没有在现有业务机执行系统全局安装。
