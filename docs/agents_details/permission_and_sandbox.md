# 权限模式、路径授权与平台级沙箱强制（完整）

> 本文由 `AGENTS.md` §10 / §10.7 / §10.8 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 10) 宿主机权限模式与沙箱执行机制（2026-05 更新）

完整说明文档：`docs/host_sandbox_and_permission_model.md`（本地文档，未随仓库发布）

为避免误解，当前系统有两套独立但叠加的控制：

1. **权限模式（Permission Mode）**：`readonly` / `approval` / `auto_approval` / `unrestricted`  
2. **执行环境（Execution Mode）**：`sandbox` / `direct`（仅宿主机模式可切换）

### 10.1 权限模式

- `readonly`
  - `run_command` 可调用，但在只读沙箱执行；写入由系统拒绝
  - 读取同样受限（各平台只读强制的可读边界见 §10.8）；读不到属预期边界，不是故障
  - `write_file` / `edit_file` 等写入类工具直接拒绝
  - 持久终端可自由创建与输入，但以只读身份运行（宿主机只读 profile），写入 EPERM 由系统强制；权限跨界切换（受限档⇄unrestricted）时现有终端直接销毁，重开生效
- `approval`
  - `run_command` 先走只读沙箱（可读边界见 §10.8，读越界也会触发权限拒绝）
  - 若出现权限拒绝（例如 `Operation not permitted` / `Permission denied`），触发前端审批
  - 审批通过后，仅该次命令以可写沙箱重试；工具结果返回“重试后的最终结果”
  - **审批只授予该次命令的工作区内写权限，不放大读取**（2026-08-30 方案一）：可写沙箱读边界与只读同一白名单，读越界唯一途径是「路径授权」；重试后仍为文件权限拒绝时，工具结果附带 `read_scope_hint` 引导模型说明
  - 以上先只读执行／权限拒绝重试规则仅适用于 sandbox。direct 下每次 `run_command` / `terminal_input` 均先人工审核参数，通过后直接执行，不进行沙箱试运行。
  - sandbox 的持久终端保持只读身份；direct 的终端为普通宿主 shell，所有输入先审批。
- `auto_approval`
  - `write_file` / `edit_file`：工作区内路径直接执行，工作区外路径进入审批流程
  - `run_command` 先走只读沙箱（可读边界见 §10.8）；触发权限拒绝后由后台审批智能体自动审核
  - 自动审核拒绝时，工具返回“被拒绝+理由”并继续主循环（不强制结束任务）
  - 批准后的读取边界与 approval 相同（不放大）
  - 沙箱分支保持原逻辑；direct 下每次命令与终端输入先自动审核，通过后直接执行。区外文件修改仍审核，工作区内编辑沿用免审规则。
- `unrestricted`
  - 普通操作权限层不做拦截：工作区内读写自由、命令免批准；是否沙箱由执行环境决定
  - 沙箱中的显式单次 full_access 申请默认人工审批；用户开启 `allow_auto_approve_full_access` 后改由审核智能体批准即可执行，无需人工。仅改变本次申请的审批方式，普通无限制操作及已在 direct 环境的命令继续免审；审批条件创建时快照。
  - 注意「无限制」只管工作区内：宿主机沙箱执行时读边界仍是白名单（见 §10.8），授权范围外读取需路径授权；direct 模式才真正无边界

### 10.2 执行环境

- `sandbox`（默认）：使用 OS 沙箱执行（macOS: `sandbox-exec`，Linux: `bwrap + seccomp`，Windows: `WSL2`）
- `direct`：支持人工审批、自动审核、无限制三种权限。只有 readonly / plan 强制沙箱，恢复／空页／Gateway 使用同一约束。只读切换继续使用 `pre_readonly_execution_mode` 恢复明确记录的原环境。
- 切换后一直生效，无自动回退机制（2026-07 已移除原 TTL 自动回退）。首次显式切入风险弹窗按用户偏好 `full_access_warning_dismissed`（默认 false）控制；确认且勾选后才保存，不按对话保存，不在恢复 direct 时重复提示。
- 审批类型 `direct_execution` 绑定执行环境、任务身份和完整参数；批准后单次原子领取完全访问作用域。`full_access` 仍专用于沙箱中的单次提权，两者不混用 grant。
- 审核智能体自己的命令绑定独立只读 sandbox scope；主对话 direct 不改变审核取证权限。下文平台终端的审批只读身份说明均限定 sandbox 环境。

### 10.3 路径授权语义

- 前端“路径授权”支持两类路径：
  - **可读可写**
  - **仅可读**
- 语义：
  - 可读集合 = 可读可写 + 仅可读
  - 可写集合 = 可读可写
- 配置来源（2026-08-30 收敛为两个）：`config/host_sandbox_policy.json`（UI 唯一读写目标）+ 真·环境变量 `HOST_SANDBOX_MACOS_WRITABLE_PATHS`（部署通道，与文件合并去重）。settings.json 的 `terminal.macos_writable_paths` 映射已移除（历史值一次性失效，需用路径授权重新添加）；.env 注入技术上仍生效，但不是受支持的通道，不推荐使用
- **工作区级授权（2026-09-27 新增）**：弹窗内分「作用域」（当前工作区 / 全局，默认当前工作区）与「类型」两个切换器，保存只写当前作用域。工作区级条目存 `host_sandbox_policy.json` 的 `workspaces` 字典（key=工作区根目录 resolve 后绝对路径，value=`{writable, readable_extra}`），合并语义**只增不减**（工作区级只能在全球基础上追加；deny 清单仍仅全球级）。生效集合 = 全球 + 当前工作区（`get_macos_writable_paths(workspace_path)` / `get_macos_readable_paths(workspace_path)`，调用方：host_sandbox_runner 沙箱 profile、file_manager 原生工具读边界）。工作区移动/重命名后其授权条目自然失效（fail-safe 方向）。该模块锁为 `threading.RLock`（save_workspace_entry 持锁期间回调 load/save_policy，普通 Lock 会自死锁）。

### 10.4 维护约束

- 不要在提示词里暴露内部实现细节（例如“命令文本猜测”等）
- 文案应面向用户能力边界与操作建议，不描述内部判定算法

### 10.5 审核智能体配置与调试

- 审核智能体配置：统一在设置页「审核智能体」分区设置（2026-08 起在个人空间，2026-09 迁设置页），存于 personalization.json 的 `review_agents` 键
  - 三个审核智能体：`auto_approval`（自动审批）/ `goal_review`（目标审核）/ `workflow_review`（工作流审核）
  - 字段：`model`（主模型注册表 key，留空=自动规则：第一个可见模型，纯 Codex 环境优先 -luna）/ `max_rounds`（默认 10）。**无产品层请求超时（2026-11-07 取消 `timeout_seconds`），HTTP 层 300s；取证命令超时固定 60 秒（取消 `max_command_timeout`）**
  - **思考固定开启、推理强度走默认（2026-11-06）**：删除 `thinking` 设置项与字段（旧值保存时被清洗丢弃），不提供推理强度设置；模型无独立思考段时沿用 fast 段配置
  - **模型调用**：Rust `ModelConfig::complete` 统一承接 Chat Completions 和 Responses。暂时性连接故障（含 TLS 握手 EOF）、断流、限流及可重试的 HTTP 状态按共享请求规则处理，首次加最多 9 次重试，固定间隔 10 秒。
  - **计费**：每次请求写入所属对话账本（`kind=review_agent`），见 `docs/conversation_api_costs.md`
  - 解析入口：`modules/review_agent_config.py::resolve_review_agent_config`（复用 `modules/aux_model_resolver.py` 统一解析主注册表；独立 `sub_agent_models.json` 库已于 2026-09 彻底废弃无兼容，子智能体/审核/标题生成模型同源）
  - 旧的独立 json 配置（`config/auto_approval.json` / `goal_review.json` / `workflow_review.json.example`）已彻底废除，无向后兼容
- 自动审核送审内容为紧凑纯文本：真实直接输入／引导／ask问答合计最近5条，调用＋结果最近5个，固定附已批准计划、用户目标和工作触发消息并去重；支持本次冻结快照的只读历史补查。不能按is_auto_generated一刀切，也不能将同批未执行调用当历史执行。
- 正常裁决approved／rejected；调用或解析故障独立error、不执行且不自动转人工；可重试错误在审核调用器内自动重试，耗尽后以 error 终结。单次完全访问开关默认关闭时，仍允许申请：auto_approval保留双批准条件，unrestricted仅人工；两种模式开启同一开关后均自动审核通过即执行。approval始终人工审批。
- 审核绑定用户输入与执行条件；新引导导致重审，批准领取和工具启动前再次核对。完整来源、分页、文本与状态契约见 `docs/auto_approval_review.md`，回归 `test/2026-11-06_自动审核上下文/`。
- 调试开关（代码变量）：`modules/approval_agent.py` 中 `DEBUG_SAVE_APPROVAL_AGENT_TRANSCRIPT`
  - 开启后写入：`logs/approval_agent/`
  - 记录以累积 `messages` 为主，便于对齐主/子智能体会话格式

### 10.7) 后台命令只读沙箱修复（2026-08-13）

后台 `run_command`（`run_in_background=true`）此前在**所有环境**绕过只读权限（`background_command_manager` 固定用可写沙箱计划）。已修复：`create_background_command` 透传 `sandbox_write_access`，Host 平台执行器按它选择只读或可写计划（见 §10.8）。

### 10.8) 平台级只读强制（2026-08-30）

只读权限的强制由各平台原生机制兜底；`config/limits.py` 的命令文本白名单（`_is_readonly_run_command_allowed`）只是**审批决策的启发式**，不再是安全边界（已知可绕过，如 `find . -delete`；绕过后果只是多走一次审批）。

- **Docker/Web 容器执行的只读强制归 Docker 版本**（2026-08-30 设计，2026-09 Landlock 云端实测记录随 fork 维护）：非特权 uid 执行角色 + Landlock 进程级只读域，仅 Linux 宿主生效；主仓 Host 不依赖该路径。
- **macOS 宿主机 = Seatbelt 白名单读模型**（`modules/host_sandbox_runner.py`）：
  - 只读/可写两个 profile **共用同一白名单读模型**（`_build_macos_whitelist_read_rules`），唯一区别是写权限：deny default + 系统路径白名单（`MACOS_MINIMAL_READABLE_PATHS`）+ 路径授权（writable + readable_extra）+ 工作区 + 祖先目录 literal allow（缺一个祖先进程 exec 直接 Abort trap，必须为 file-read*）+ env 注入 `GIT_CONFIG_GLOBAL=/dev/null`（可写/只读/持久终端三条 plan 均注入）
  - **xcselect 垫片兼容（2026-09-07 修复）**：/usr/bin 下的 python3/git/clang 等是 xcselect 垫片，启动即拉起 xcodebuild 读 `/Library/Preferences/com.apple.dt.Xcode.plist` 校验 license、并经 mach 服务 `com.apple.bsd.dirhelper` 解析 DARWIN_USER_TEMP_DIR——8-30 白名单化曾致其在沙箱内全灭。现两个 profile 均内置 `MACOS_BASE_MACH_RULES`（放行 dirhelper），白名单含 `/Library/Preferences`；可写 profile 额外放行 `$TMPDIR` 父目录（xcrun_db 缓存写入），只读 profile 不放行（缓存写失败仅噪音、非致命）
  - **deny 规则（.env 正则、~/.ssh 等）必须位于所有 allow 之后**（Seatbelt 后规则覆盖先规则）；两个 profile 均已修复旧顺序漏洞（工作区 .env 曾实际可读）
  - 可写 profile 已于 2026-08-30 白名单化（此前为全局可读，导致 unrestricted/审批批准后能读授权范围外文件）；白名单固有代价：祖先目录顶层文件名可列出（读文件内容仍被拒）
  - **受限网络档放开本机监听（2026-11-18）**：`_build_macos_network_policy` 的 restricted 档在回环出站之外加 `network-bind` + `network-inbound`（均为 `localhost`），沙箱内可起 localhost 服务；可写 profile 另按可写目录放行 Unix socket（bind + connect，目录外仍 EPERM），只读 profile 不放 Unix socket。真机实测边界：只放 bind 时 `listen()` 仍 EPERM；Seatbelt 只在 `listen()` 时检查 inbound、不按来源过滤，且 `localhost` 过滤器也放行 `0.0.0.0`/`::` 绑定——服务监听全部地址时局域网可访问，profile 层无法禁止，由网络说明行（`mode.py::_build_network_rules_line`，必须单行）要求模型显式监听 127.0.0.1。出站仍只限回环。full 档规则未改。证据 `test/2026-11-18_macOS沙箱本机监听/`
  - 持久终端 shell plan 支持 readonly 参数（`_build_macos_shell_plan` 复用 `_macos_readonly_profile_for_workspace`）：受限档终端以只读 profile 创建，unrestricted 保持可写 profile
  - 原生文件工具对齐（`file_manager/path_mixin.py`）：读 roots 与沙箱白名单同源（系统路径 + 工作区 + 授权）+ 叠加同一禁读清单——至此 host+sandbox 下全部读通道（只读/可写沙箱命令、原生 read_file）共享同一边界
- **Linux 宿主机（2026-10）= 管理员安装的受限助手**：bwrap 最小文件系统 + seccomp + AppArmor + systemd/cgroup IP 过滤；不再挂载宿主 `/`。共享 host 网络保证双向 localhost；restricted 允许回环和授权路径 Unix socket、阻断抽象 Unix socket；none 保留匿名 stream/seqpacket socketpair。最后降到真实普通 UID 并清能力。首批 Ubuntu 24.04/kernel 6.8 隔离实验通过，系统全局安装和其它发行版仍待验收。原生 IO 与精确系统白名单同源，不给实际宿主 `/tmp` 隐式授权。具体约束见 §20。
- **Windows = WSL2 最小根文件系统（白名单）**——命名空间内只有系统目录+工作区，天然符合。

### 私有工作区记忆的内置路径能力（2026-11-07）

用户配置来源仍为环境变量与路径授权文件；此外，受信任的 FileManager 资源装配会注册当前工作区对应的外置记忆目录。`get_macos_readable_paths`／`get_macos_writable_paths` 动态并入该精确目录，复用三平台沙箱与原生 IO 的共同入口，不写回用户配置、不授权共享数据根。子实例只追加此隐式读根，固定窄写根和 readonly/plan 限制不变。“主写子读”是默认维护策略，不声称缩小 full_access 等既有能力。已全文注入的主对话文件免工具预读、auto_approval 下按内置工作区资源放行，人工审批仍沿用原规则。完整说明见 `docs/workspace_memory.md`。
