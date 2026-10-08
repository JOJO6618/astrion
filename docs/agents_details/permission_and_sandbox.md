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
  - 持久终端可自由创建与输入，但以只读身份运行（docker 非特权 uid / 宿主机只读 profile），写入 EPERM 由系统强制；权限跨界切换（受限档⇄unrestricted）时现有终端直接销毁，重开生效
- `approval`
  - `run_command` 先走只读沙箱（可读边界见 §10.8，读越界也会触发权限拒绝）
  - 若出现权限拒绝（例如 `Operation not permitted` / `Permission denied`），触发前端审批
  - 审批通过后，仅该次命令以可写沙箱重试；工具结果返回“重试后的最终结果”
  - **审批只授予该次命令的工作区内写权限，不放大读取**（2026-08-30 方案一）：可写沙箱读边界与只读同一白名单，读越界唯一途径是「路径授权」；重试后仍为文件权限拒绝时，工具结果附带 `read_scope_hint` 引导模型说明
  - **执行环境锁定为沙箱**（2026-08-30 起）：direct 下无沙箱兜底，启发式漏判的写命令会直接执行成功、审批承诺被架空，故批准/自动审核与 direct 硬互斥（锁定机制同 plan/只读，见 §10.6）
  - 持久终端以只读身份运行（同 readonly 条目）：终端内写入永远 EPERM，需审批的写入走 `run_command` 两段式通道
- `auto_approval`
  - `write_file` / `edit_file`：工作区内路径直接执行，工作区外路径进入审批流程
  - `run_command` 先走只读沙箱（可读边界见 §10.8）；触发权限拒绝后由后台审批智能体自动审核
  - 自动审核拒绝时，工具返回“被拒绝+理由”并继续主循环（不强制结束任务）
  - 批准后的读取边界与 approval 相同（不放大）
  - 执行环境同样锁定为沙箱（同 approval 条目）
- `unrestricted`
  - 权限层不做拦截：工作区内读写自由、命令免批准；是否沙箱由执行环境决定
  - 注意「无限制」只管工作区内：宿主机沙箱执行时读边界仍是白名单（见 §10.8），授权范围外读取需路径授权；direct 模式才真正无边界

### 10.2 执行环境

- `sandbox`（默认）：使用 OS 沙箱执行（macOS: `sandbox-exec`，Linux: `bwrap + seccomp`，Windows: `WSL2`）
- `direct`：宿主机直接执行（高风险），**仅 `unrestricted` 权限可选**：受限权限档（只读/批准/自动审核）与 plan 模式下后端 `set_execution_mode` 硬锁拒绝 direct（i18n 键 `main_terminal.restricted_mode_locks_sandbox` / `plan_mode_locks_sandbox`）；进入受限档时若已是 direct 会被联动压回沙箱（记录 `pre_readonly_execution_mode`，切回 unrestricted 时恢复）；存量「受限档+direct」对话在加载时自愈矫正回沙箱
- 切换后一直生效，无自动回退机制（2026-07 已移除原 TTL 自动回退）

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
  - 字段：`model`（主模型注册表 key，留空=自动规则：第一个可见模型，纯 Codex 环境优先 -luna）/ `thinking` / `timeout_seconds` / `max_rounds` / `max_command_timeout`
  - 解析入口：`modules/review_agent_config.py::resolve_review_agent_config`（复用 `modules/aux_model_resolver.py` 统一解析主注册表；独立 `sub_agent_models.json` 库已于 2026-09 彻底废弃无兼容，子智能体/审核/标题生成模型同源）
  - 旧的独立 json 配置（`config/auto_approval.json` / `goal_review.json` / `workflow_review.json.example`）已彻底废除，无向后兼容
- 调试开关（代码变量）：`modules/approval_agent.py` 中 `DEBUG_SAVE_APPROVAL_AGENT_TRANSCRIPT`
  - 开启后写入：`logs/approval_agent/`
  - 记录以累积 `messages` 为主，便于对齐主/子智能体会话格式

### 10.7) 后台命令只读沙箱修复（2026-08-13）

后台 `run_command`（`run_in_background=true`）此前在**所有环境**绕过只读权限（`background_command_manager` 固定用可写沙箱计划）。已修复：`create_background_command` 透传 `sandbox_write_access`，宿主机路径按它选只读/可写计划；docker 路径按它决定是否以非特权 uid 执行（见 §10.8）。

### 10.8) 平台级只读强制（2026-08-30）

只读权限的强制由各平台原生机制兜底；`config/limits.py` 的命令文本白名单（`_is_readonly_run_command_allowed`）只是**审批决策的启发式**，不再是安全边界（已知可绕过，如 `find . -delete`；绕过后果只是多走一次审批）。

- **docker/web 模式 = 非特权 uid 执行角色 + Landlock 进程级只读域**（`modules/docker_readonly_exec.py`）：
  - 容器主进程保持 root（可写执行不变）；`sandbox_write_access=False` 的执行通道（`terminal_ops/run.py`、`background_command_manager.py`、只读语境创建的持久终端）以 `-u 10001:10001` 运行，`DOCKER_READONLY_EXEC_UID/GID` 可覆盖
  - 第一层强制力 = 内核 DAC：工作区属主为宿主机 root，非属主无写权；600 权限文件（如 .env）不可读；逃逸需提权（setuid/内核漏洞），无 umount 类捷径
  - **第二层 = Landlock 只读域**（2026-09 新增，云端实测 kernel 6.8 / ABI V4 / Docker 28 默认 seccomp 放行）：纯 DAC 的残留漏洞是工作区内历史遗留的 world-writable（777/o+w）路径对只读 uid 仍可写；只读执行时命令再经 `modules/landlock_launcher.py`（首次执行时 docker cp 进容器并自检）进入「工作区写类操作全拒」的内核域，最终权限 = DAC ∩ Landlock，该洞封死。要点：
    - launcher 规则：handled 只含写类操作；ro 路径（工作区挂载点）不加规则（无覆盖即拒绝）；/tmp、/var/tmp、/dev/shm 显式授写权以对齐纯 DAC 现状行为（只读身份 HOME=/tmp）；读/执行不进 handled，仍由 DAC 管。注意不能写「/ 授全量 + ro 授空」的交集规则——空授权规则被内核拒绝（ENOMSG/errno 42）
    - 自检失败（内核 <5.13 / seccomp 拦截 / 容器无 python3）自动降级纯 DAC，warning 日志标注 enforcement level；`DOCKER_READONLY_LANDLOCK=0` 可整体停用
    - 语义边界：Landlock 不管 chmod/chown 等元数据修改，也不管未配置的网络——因此非特权 uid 层必须保留（root+Landlock 的进程可 chmod 放宽权限位让域外进程受益）
  - 前提：工作区属主与该 uid 不碰撞、容器未挂 docker.sock（云端已验证）；macOS Docker Desktop 双重不适用（virtiofs fakeowner 不按 uid 检查；linuxkit 内核未编译 Landlock），仅 Linux 宿主生效
  - 持久终端在 readonly/approval/auto_approval 下同以只读身份创建（`terminal_readonly_enabled` 判定，`terminal_readonly_getter` 注入）；终端里的写入会被拒，写命令走 run_command 审批通道；权限跨界切换（受限档⇄unrestricted）时销毁现有终端会话重建（`_apply_restricted_execution_mode_link` → `close_all()`）
  - Dockerfile 创建 `agent` 用户 + `/etc/gitconfig` safe.directory + 去 setuid 加固；数字 uid 不依赖镜像内用户存在，旧镜像直接受益
- **macOS 宿主机 = Seatbelt 白名单读模型**（`modules/host_sandbox_runner.py`）：
  - 只读/可写两个 profile **共用同一白名单读模型**（`_build_macos_whitelist_read_rules`），唯一区别是写权限：deny default + 系统路径白名单（`MACOS_MINIMAL_READABLE_PATHS`）+ 路径授权（writable + readable_extra）+ 工作区 + 祖先目录 literal allow（缺一个祖先进程 exec 直接 Abort trap，必须为 file-read*）+ env 注入 `GIT_CONFIG_GLOBAL=/dev/null`（可写/只读/持久终端三条 plan 均注入）
  - **xcselect 垫片兼容（2026-09-07 修复）**：/usr/bin 下的 python3/git/clang 等是 xcselect 垫片，启动即拉起 xcodebuild 读 `/Library/Preferences/com.apple.dt.Xcode.plist` 校验 license、并经 mach 服务 `com.apple.bsd.dirhelper` 解析 DARWIN_USER_TEMP_DIR——8-30 白名单化曾致其在沙箱内全灭。现两个 profile 均内置 `MACOS_BASE_MACH_RULES`（放行 dirhelper），白名单含 `/Library/Preferences`；可写 profile 额外放行 `$TMPDIR` 父目录（xcrun_db 缓存写入），只读 profile 不放行（缓存写失败仅噪音、非致命）
  - **deny 规则（.env 正则、~/.ssh 等）必须位于所有 allow 之后**（Seatbelt 后规则覆盖先规则）；两个 profile 均已修复旧顺序漏洞（工作区 .env 曾实际可读）
  - 可写 profile 已于 2026-08-30 白名单化（此前为全局可读，导致 unrestricted/审批批准后能读授权范围外文件）；白名单固有代价：祖先目录顶层文件名可列出（读文件内容仍被拒）
  - 持久终端 shell plan 支持 readonly 参数（`_build_macos_shell_plan` 复用 `_macos_readonly_profile_for_workspace`）：受限档终端以只读 profile 创建，unrestricted 保持可写 profile
  - 原生文件工具对齐（`file_manager/path_mixin.py`）：读 roots 与沙箱白名单同源（系统路径 + 工作区 + 授权）+ 叠加同一禁读清单——至此 host+sandbox 下全部读通道（只读/可写沙箱命令、原生 read_file）共享同一边界
- **Linux 宿主机（2026-10）= 管理员安装的受限助手**：bwrap 最小文件系统 + seccomp + AppArmor + systemd/cgroup IP 过滤；不再挂载宿主 `/`。共享 host 网络保证双向 localhost；restricted 允许回环和授权路径 Unix socket、阻断抽象 Unix socket；none 保留匿名 stream/seqpacket socketpair。最后降到真实普通 UID 并清能力。首批 Ubuntu 24.04/kernel 6.8 隔离实验通过，系统全局安装和其它发行版仍待验收。原生 IO 与精确系统白名单同源，不给实际宿主 `/tmp` 隐式授权。具体约束见 §20。
- **Windows = WSL2 最小根文件系统（白名单）**——命名空间内只有系统目录+工作区，天然符合。
