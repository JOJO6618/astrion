# Windows 沙箱执行不可信命令：开源组件/项目调研报告

> 调研日期：2026-07（以实际检索为准）；目标场景：Python 智能体运行时在 Windows 上执行不可信命令（一次性命令 + 交互式 shell）。
> 需求速记：**R1** 文件只读模式（工作区以外只读）、**R2** 工作区可写、**R3** 敏感路径禁读、**R4** 网络三档（禁网/仅回环/全开）、**R5** Python asyncio subprocess 可集成。
> 所有结论均来自本轮实际检索，检索不到的已明确标注。

---

## 0. 结论速览（先看这个）

| 方案 | 一句话结论 |
|---|---|
| **OpenAI Codex 的 windows-sandbox-rs** | 需求匹配度最高的"参考答案"：restricted token + ACL + WFP 防火墙，五条需求全覆盖；Apache-2.0，但是 Rust 代码，Python 侧需要借鉴重写或把 codex CLI 当子进程调用 |
| **GRR `unprivileged/windows/sandbox_lib.py`** | 唯一一个**纯 Python（pywin32+ctypes）的 AppContainer 沙箱实现**，Apache-2.0，核心代码量小，可直接 vendor；但无网络控制、写授权模型需补齐 |
| **Sandboxie-Plus** | 隔离粒度最细（文件/注册表/网络逐规则配置），命令行可驱动；GPL-3.0 且依赖内核驱动，**不能嵌入闭源产品**，只能作为"用户自行安装的外部依赖"调用 |
| **Windows Sandbox（.wsb）** | 微软官方一次性 VM，映射文件夹只读/可写、可禁网，但**无 stdout/stderr 捕获管道、无编程 API**，不适合 agent 自动化 |
| **Windows Containers（containerd/runhcs）** | 不用 Docker 可以跑，但镜像 260MB~5GB+、process isolation 要求镜像与宿主 build 严格匹配，运维成本高，不适合桌面 agent 场景 |
| **Dokan/WinFsp 只读视图** | 只解决 R1（只读视图），不提供进程隔离与网络控制，需叠加 token/ACL；属于"可选增强"而非独立方案 |
| **winjail / Firejail Windows 版** | **查不到**，不存在可用品（详见第 3 节） |

**推荐路线**：以 Codex 的 Windows 沙箱设计（restricted token + 合成 SID + ACL 授权 + WFP 网络过滤）为蓝本，用 pywin32/ctypes 在 Python 内实现（GRR 代码可作起点）；把 Sandboxie 作为可选的外部加固后端。

---

## 1. Python 可用的 Windows 沙箱库

### 1.1 GRR `grr_response_client.unprivileged.windows`（本轮最大发现）

- 链接：https://github.com/google/grr （模块路径 `grr/client/grr_response_client/unprivileged/windows/sandbox_lib.py`）
- 维护状态：google/grr 仓库 5084 star，Apache-2.0，最近提交 2026-05（活跃）
- 这是什么：一个**纯 Python 的 Windows AppContainer 沙箱库**，docstring 原文："Windows Sandboxing library based on AppContainers… works only on Windows >= 8"。用 ctypes 调 `userenv.CreateAppContainerProfile` / `DeriveAppContainerSidFromAppContainerName`，用 pywin32（`win32security`/`win32service`/`ntsecuritycon`）做 ACL 授权，并创建独立的 window station/desktop。
- 能力：`InitSandbox(name, paths_read_only)` 创建 AppContainer 并对指定路径授予 `GENERIC_READ|GENERIC_EXECUTE`；`Sandbox` 类管理 window station/desktop 生命周期；同目录 `process.py`（9KB）负责在沙箱里拉起进程。PyInstaller issue #8289 中有真实用户用它把 exe 跑进 AppContainer 的实例。
- 需求覆盖：R1 ✅（未授权路径默认拒读写）、R3 ✅（不授权即禁读）、R5 ✅（纯 Python，可与 asyncio 集成）；R2 ⚠️（示例只演示只读授权，可写授权需要同样用 ACL 加 `GENERIC_WRITE`，代码路径现成）；R4 ❌（无网络控制，需自行叠加 WFP）。
- 注意：它不是独立 PyPI 包，需要 vendor 该文件（Apache-2.0 允许），依赖 pywin32。

### 1.2 pywin32 本身

- 链接：https://github.com/mhammond/pywin32
- 已核实源码包含 `win32job.i`（Job Object：CreateJobObject/SetInformationJobObject/AssignProcessToJobObject）、`win32security.i`（restricted token、ACL）、`win32process.i`。即"restricted token + Job Object + ACL"三件套在 Python 里**不需要写 C 扩展**就能拼出来——这是自研路线的地基。
- 许可：PSF 风格，闭源友好。

### 1.3 PyPI 上的候选包（均已核实）

| 包名 | 状态 |
|---|---|
| `sandbox` 0.3.5 | 存在，但简介为 "The Sandbox Libraries (Python)"——是早年 Linux seccomp-nurse 的绑定，**与 Windows 无关** |
| `pyjail` 0.1.4 | 存在，MIT，是**进程内** Python 代码沙箱（非 OS 级），不满足需求 |
| `winsandbox` / `appcontainer` / `winjail` / `seccomp` | **404，不存在** |

结论：PyPI 上**没有**成熟的、封装 AppContainer/Job Object/restricted token 的 Windows 沙箱包。成熟度最高的可直接借鉴物就是 GRR 的那个模块。

---

## 2. Sandboxie / Sandboxie-Plus

- 链接：https://github.com/sandboxie-plus/Sandboxie （19034 star，**GPL-3.0**，2026-07-29 仍有提交，非常活跃；社区 fork，维护者 David Xanatos，项目历史 Ronen Tzur → Invincea → Sophos → 2020 开源）
- 命令行驱动：✅ 有官方文档（https://sandboxie-plus.github.io/sandboxie-docs/Content/StartCommandLine.html ）：
  - `"C:\Program Files\Sandboxie\Start.exe" /box:MyBox /wait /hide_window cmd.exe /c xxx`——`/wait` 会等进程结束并**透传退出码**；
  - `/terminate` / `/terminate_all` 可清空沙箱内进程。
  - **stdout 捕获有坑**：文档明确 "Start.exe is a Win32 application and not a console application"，即它**不转发子进程 stdio**。可行做法是在沙箱内重定向：`Start.exe /box:X /wait cmd /c "cmdline > C:\boxpath\out.txt 2>&1"`，然后从宿主侧读沙箱文件系统根目录下的输出文件。交互式 shell 同理需要走管道桥接，做不到 `asyncio.create_subprocess_exec` 那种原生 PIPE 体验。
- 隔离粒度（文档核实）：
  - 文件：`ClosedFilePath`（**禁读禁写**，官方文档明确 "deny all access by sandboxed programs, including read"）、`ReadFilePath`（可读、写入被虚拟化）、`OpenFilePath`（直通直写）；默认所有写都落在沙箱虚拟层（copy-on-write），不污染宿主。
  - 注册表：`ClosedKeyPath` 等同类规则。
  - 网络：Plus 版内置**基于 WFP 的每沙箱防火墙**（`NetworkEnableWFP=y` + `NetworkAccess=<program>,Block/Allow;Protocol=...`）。Issue #5109（https://github.com/sandboxie-plus/Sandboxie/issues/5109 ）正是"禁外网但放行本地代理（回环）"的配置讨论——三档网络均可表达。
- 许可与嵌入：GPL-3.0 + 需要安装**签名内核驱动 + 系统服务**（SbieDrv/SbieSvc）。结论：**不能以库形式嵌入闭源产品**（GPL 传染性 + 驱动安装要求）。合法用法是检测用户机器上是否已装 Sandboxie，有则调用，没有则提示安装——属于外部可选依赖。
- 其他 agent 项目使用情况：没有找到把 Sandboxie 作为嵌入式沙箱后端的知名 agent 项目；但有用户自发把 Cursor 整个 IDE 跑进 Sandboxie 的实践（Discussion #5077，https://github.com/sandboxie-plus/Sandboxie/discussions/5077 ）。
- 需求覆盖：R1 ✅（ReadFilePath+虚拟化）、R2 ✅（OpenFilePath 或沙箱内写+回收）、R3 ✅（ClosedFilePath）、R4 ✅（WFP 三档可配）、R5 ⚠️（一次性命令可行但 stdout 要走文件；交互式 shell 需自建桥接）。

---

## 3. nsjail / minijail / bwrap / Firejail 的 Windows 等价物

- **winjail**：GitHub 全库搜索（API 实测）只命中一个不相关的玩笑项目 `netcrawlerr/WinJail`（2 star，WinForms 弹窗玩具）。**作为沙箱项目的 winjail 查不到，不存在。**PyPI 同名也 404。
- **Firejail**：https://github.com/netblue30/firejail 明确是 "Linux namespaces and seccomp-bpf sandbox"，**没有 Windows 移植**，issue 区讨论也确认无 Windows 支持。
- **nsjail**（https://github.com/google/nsjail ）：基于 Linux namespaces/cgroups/seccomp-bpf，Linux 专用。
- **minijail**：ChromiumOS 项目，同样 Linux 专用。
- Windows 生态里事实上的"等价物"是三样东西的组合：**AppContainer（能力沙箱）+ restricted token/完整性级别（MIC）+ Job Object（资源限制）**。Chromium 的 Windows sandbox 是这套组合最著名的工程实现；2026 年 GSoC 有个面向 Gemini CLI 的 AppContainer PoC（https://github.com/AnushkaPandit-21/appcontainer-node-sandbox ，学生 PoC，C++/Node-API 实现，验证了 `CreateAppContainerProfile`+ACL+`PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES` 全链路），可参考但成熟度低。

---

## 4. 容器路线：Windows Containers（不装 Docker）

- 官方文档：https://learn.microsoft.com/en-us/virtualization/windowscontainers/manage-containers/hyperv-container ——process isolation 与 Hyper-V isolation 两种。
- **不装 Docker 能不能跑**：能。Windows 10 1809+ / Server 2019+ 内置 HCS（Host Compute Service），可用 **containerd + runhcs** 直接跑 OCI 容器（TechTarget 综述、James Sturtevant 的实操博客、docker/roadmap#151 均有 walkthrough）。hcsshim（Go 库）是底层 API 封装。
- 坑（做命令沙箱的硬伤）：
  1. **process isolation 要求镜像与宿主内核 build 严格匹配**（https://jfreeman.dev/blog/2021/09/01/how-to-run-a-windows-container-from-a-windows-10-virtual-machine/ ），用户机器 build 号千奇百怪，镜像要么全量预置要么 fallback 到 Hyper-V isolation（需要启用 Hyper-V/虚拟机平台，专业版以上）。
  2. **镜像体积**：Nano Server ~260–290MB、Server Core 解压后 ~4.8–6.9GB（ltsc2022 5.16GB、ltsc2025 6.86GB，https://github.com/microsoft/Windows-Containers/issues/584 ）。首次拉取 GB 级，冷启动数秒到十数秒。
  3. Python 集成只能走 `ctr`/`runhcs` CLI 子进程或自研 hcsshim 绑定。
- 需求覆盖：R1/R2/R3 ✅（volume mount 可 read-only）、R4 ⚠️（`--network none` 可禁网，"仅回环"需自建 HNS 网络策略）、R5 ⚠️（经 CLI 间接集成）。**结论：技术上可行，但对"桌面 agent 跑一条命令"的场景过重，不推荐。**

---

## 5. 特殊文件系统过滤：Dokan / WinFsp 只读视图

- WinFsp：https://github.com/winfsp/winfsp （8774 star，活跃，许可为 **GPLv3 + 商业双许可**，GitHub API 返回 NOASSERTION 即自定义/双许可）
- Dokany：https://github.com/dokan-dev/dokany （仓库内含 `license.lgpl.txt` 与 `license.mit.txt`，用户态库 LGPL、部分组件 MIT）
- 可行性：两者都能在用户态实现一个"把宿主目录映射成只读视图"的虚拟盘（WinFsp 官方 passthrough 教程就是现成起点；只读语义由 FS 实现对写操作返回拒绝即可，cgofuse issue #15 讨论了 Windows 上 `-o ro` 的实现方式）。
- **但作为沙箱手段有关键缺陷**：只读视图管不住"进程直接打开原始路径"——沙箱内进程只要还有权限访问原路径就能绕过挂载点。所以它**不能替代 token/ACL 级隔离**，只能做：把敏感路径"藏起来不给视图"+ 配合 AppContainer/restricted token 收回原路径访问权。此外还需要安装内核驱动（分发负担 + WinFsp 的 GPL/商业许可成本）。
- 需求覆盖：R1 ✅（仅视图层面）、R2 ⚠️（叠加 overlay 可实现写重定向）、R3 ⚠️（需配合 token）、R4 ❌、R5 ❌（与进程管理无关）。
- **结论：可选增强项，不是独立方案。**若最终走 ACL 路线，本项可以跳过。

---

## 6. 微软官方 2023–2025 的新东西

1. **Win32 App Isolation**（Build 2023 宣布、2024 持续推）：
   - Windows Blog 2023-06 公测公告（https://blogs.windows.com/windowsdeveloper/2023/06/14/public-preview-improve-win32-app-security-via-app-isolation/ ）："built on AppContainers"；微软还专门发文 Sandboxing Python with Win32 App Isolation（https://blogs.windows.com/windowsdeveloper/2024/03/06/sandboxing-python-with-win32-app-isolation/ ，2024-03）。
   - 要点：面向 **MSIX 打包应用**的隔离（能力声明式授权、隐私式提示），不是一个"给任意命令行进程套沙箱"的 API。对 agent 运行时场景：理念可借鉴，接口形态不匹配。
2. **Windows Sandbox（消费级功能）+ .wsb 配置**：
   - MS Learn 配置文档（https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file ，本轮全文核实）：`<MappedFolders>` 支持 `ReadOnly=true/false`、`<Networking>Enable/Disable`、`<LogonCommand>` 开机执行一条命令、内存上限。Win10 18342+/Win11，**仅 Pro/Enterprise/Education，Home 不可用**。
   - 致命短板：**没有任何 stdout/stderr/退出码回传机制**，是 RDP 交互桌面；启动是完整 VM（秒级~十秒级）；无编程 API。适合人工试毒，不适合 agent 自动化管道。
3. **Agent Workspace（Windows 11，2025-10/11 起面向 Insider 推出）**：
   - 来源：https://4sysops.com/archives/what-is-agent-workspace-in-windows-11/ 、https://support.microsoft.com/en-us/windows/ai/ai-features/experimental-agentic-features 、https://techcommunity.microsoft.com/blog/windows-itpro-blog/evolving-windows-new-copilot-and-ai-experiences-at-ignite-2025/4469466 。
   - 要点：给 AI agent 一个**独立的 Windows 会话**——每个 agent 有自己的账户、桌面，"runtime isolation and scoped authorization"，策略可控、可审计。这是微软官方对"agent 沙箱"的回答，方向上与 Codex 的"专用本地账户"思路一致。目前为实验性、Insider 通道，**尚无公开的第三方可编程 API**，属于"盯着看"项。
   - 旁证：Codex 评估过 Windows Sandbox 和 MIC 后放弃（见第 7 节），说明微软现有官方原语确实拼不出开箱即用的 agent 沙箱。

---

## 7. Codex 的 Windows 实现（openai/codex 仓库核实）

- 仓库：https://github.com/openai/codex （102379 star，**Apache-2.0**，极活跃）
- 源码位置：`codex-rs/windows-sandbox-rs/`，本轮用 GitHub API 核实了完整文件清单，关键模块：`acl.rs`(27KB)、`token.rs`(18KB)、`cap.rs`、`proc_thread_attr.rs`、`process.rs`、`desktop.rs`、`identity.rs`、`elevated/elevated_impl.rs`、`wfp.rs`(16KB)+`wfp_setup.rs`、`deny_read_*.rs`、`setup.rs`(77KB)、`conpty/`、`unified_exec/`，外加 `sandbox_smoketests.py`（Python 冒烟测试）。
- 架构（InfoQ 2026-06 报道 https://www.infoq.com/news/2026/06/codex-windows-sandbox-design/ + 仓库 issue/讨论交叉验证）：
  - **评估后放弃** Windows Sandbox（要直接访问开发者工作区、且非全 SKU 可用）与单纯 MIC。
  - **unelevated 沙箱**（第一版）：当前用户上下文下用 **restricted token + 合成 SID "sandbox-write"**，只对工作区和显式配置的目录授写权限；`.git` 等敏感元数据用 ACL 拒读（`deny_read_*` 系列文件就是干这个的）。
  - **elevated 沙箱**（现行版）：安装时创建**专用本地账户**（CodexSandboxOffline / CodexSandboxOnline），命令以这些账户 + restricted token 运行（`CreateProcessWithLogonW`），**网络用 WFP 防火墙规则**控制——offline 账户禁网、online 账户放网，正好对应"禁网/全开"；配置项 `[windows] sandbox = "elevated"|"unelevated"`（issue #25566、#33073 等可佐证）。
  - `conpty/` 与 `unified_exec/` 目录的存在说明他们 ship 了**交互式 PTY 会话**能力（对应"交互式 shell"需求）。
- 工程教训（从 issue 区看到的真实代价）：setup helper 需要条件 ACE 规避 UAC（issue #26087 的 740 错误）、配置文件损坏导致 `CreateProcessWithLogonW: 1168`（#33073）、helper 进程泄漏（#32194）。说明这条路**能走通但边角多**，自研需要预留测试预算。
- 需求覆盖：**R1 ✅ R2 ✅ R3 ✅ R4 ✅（WFP 三档，仅回环可用 WFP 规则表达）R5 ⚠️**（Rust crate，不是 Python 库；Python 集成路径有两条：① 把 `codex sandbox -- <cmd>` 当外部子进程调用——最省事但引入重依赖；② 以它为蓝本用 pywin32/ctypes 重写核心——token+ACL 部分 GRR 已给出 Python 模板，WFP 部分（`fwpuclnt.dll`）需自行绑定，工作量集中在网络档）。

---

## 8. 需求 × 方案 覆盖矩阵

| 方案 | R1 只读 | R2 工作区可写 | R3 敏感路径禁读 | R4 网络三档 | R5 asyncio 集成 | 许可 | 嵌入闭源产品 |
|---|---|---|---|---|---|---|---|
| 自研（Codex 蓝本 + pywin32/ctypes） | ✅ ACL | ✅ 授权写 | ✅ deny ACL | ✅ WFP | ✅ 原生 | 自研 | ✅ |
| GRR sandbox_lib（vendor） | ✅ | ⚠️ 需补写授权 | ✅ | ❌ 需补 WFP | ✅ | Apache-2.0 | ✅ |
| Sandboxie-Plus（外部调用） | ✅ | ✅ | ✅ | ✅ WFP | ⚠️ stdout 走文件 | GPL-3.0 + 驱动 | ❌ 仅外部依赖 |
| Windows Sandbox (.wsb) | ✅ | ✅ | ⚠️ 只映射该映射的 | ⚠️ 只有开/关 | ❌ 无 stdio | OS 内置 | — |
| Windows Containers (runhcs) | ✅ | ✅ | ✅ | ⚠️ 回环档麻烦 | ⚠️ CLI 间接 | Apache-2.0 组件 | ⚠️ 运维重 |
| Dokan/WinFsp 只读视图 | ✅ 仅视图 | ⚠️ overlay | ⚠️ 需配合 token | ❌ | ❌ | GPL/商业 或 LGPL/MIT | ⚠️ |
| PyPI 现成包 | — | — | — | — | — | — | **不存在** |

## 9. 落地建议（给运行时的技术选型）

1. **主路线（自研，强烈推荐）**：以 Codex windows-sandbox-rs 为设计蓝本、以 GRR sandbox_lib.py 为 Python 代码起点：
   - 文件隔离：AppContainer 或 restricted token + 合成 SID，工作区授 `GENERIC_ALL`，其余默认拒写；敏感路径（`~/.ssh`、`~/.aws`、浏览器凭据目录等）显式 deny-read ACE；
   - 网络三档：ctypes 绑 `fwpuclnt.dll` 加 WFP 过滤器（禁网=block all、仅回环=allow 127.0.0.1/::1、全开=不加规则）；Codex 的 wfp.rs 可作逻辑参照；
   - 进程管理：Job Object（pywin32 `win32job` 现成）做内存/进程数限制与树杀；
   - asyncio 集成：`asyncio.create_subprocess_exec` 无法直接带 token 启动，需要先用同步 API CreateProcess* 再包装句柄，或封装成 `loop.run_in_executor` + 管道线程；交互 shell 参考 Codex 的 ConPTY 方案（Python 侧有 `pywinpty` 可用）。
2. **可选加固后端**：检测用户已安装 Sandboxie 时提供"在 Sandboxie 中运行"选项（GPL 不构成对闭源主程序的传染，因为是外部进程调用而非链接）。
3. **不要选**：Windows Containers（重）、Windows Sandbox（无 stdio）、WinFsp/Dokan 单用（不隔离进程）、等待 winjail 类项目（不存在）。
4. **值得关注**：Windows 11 Agent Workspace 的 API 开放情况——一旦微软开放第三方接入，可能成为官方托管方案。

---

## 附：主要信息来源

- OpenAI Codex 仓库：https://github.com/openai/codex ；InfoQ 报道：https://www.infoq.com/news/2026/06/codex-windows-sandbox-design/
- GRR：https://github.com/google/grr ；sandbox_lib.py 源码：https://raw.githubusercontent.com/google/grr/master/grr/client/grr_response_client/unprivileged/windows/sandbox_lib.py
- Sandboxie：https://github.com/sandboxie-plus/Sandboxie ；命令行文档：https://sandboxie-plus.github.io/sandboxie-docs/Content/StartCommandLine.html ；ClosedFilePath：https://sandboxie-plus.github.io/sandboxie-docs/Content/ClosedFilePath.html ；WFP 网络配置 issue：https://github.com/sandboxie-plus/Sandboxie/issues/5109
- Windows Sandbox 配置：https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file
- Windows Containers：https://learn.microsoft.com/en-us/virtualization/windowscontainers/manage-containers/hyperv-container ；镜像尺寸：https://github.com/microsoft/Windows-Containers/issues/584
- Win32 App Isolation：https://blogs.windows.com/windowsdeveloper/2023/06/14/public-preview-improve-win32-app-security-via-app-isolation/ ；Python 沙箱化：https://blogs.windows.com/windowsdeveloper/2024/03/06/sandboxing-python-with-win32-app-isolation/
- Agent Workspace：https://4sysops.com/archives/what-is-agent-workspace-in-windows-11/ ；https://techcommunity.microsoft.com/blog/windows-itpro-blog/evolving-windows-new-copilot-and-ai-experiences-at-ignite-2025/4469466
- WinFsp：https://github.com/winfsp/winfsp ；Dokany：https://github.com/dokan-dev/dokany
- GSoC AppContainer PoC：https://github.com/AnushkaPandit-21/appcontainer-node-sandbox
