# 定时任务核心逻辑回归（clock / schema / store / service / runtime adapter / 通知 / HTTP API / Agent 工具）

2026-11-16。本目录只包含测试与辅助文件，不修改 `modules/scheduled_tasks/` 与 `server/` 实现。
全部用例使用 `tempfile` 临时目录；`test_runtime_adapter.py` 与 `test_schedules_api.py` 在导入
config/server 前设置 `ASTRION_IGNORE_DOTENV=1`，并把 `ASTRION_DATA_ROOT`、`DATA_DIR`、`LOGS_DIR`、
`USER_SPACE_DIR`、`API_USER_SPACE_DIR`、`DEPLOY_CONFIG_DIR` 指向临时目录，不读真实 `.env`、用户数据或凭据。
runtime 测试注册的 `test-model` 使用字面量假 apikey，仅通过模型能力校验，不会发起网络请求。
`serve_isolated.py` 是手动/浏览器联调用的合成模型隔离服务（非 unittest，不读取真实凭据）。

## 运行

```bash
# 在工作树根目录执行
/opt/homebrew/opt/python@3.11/bin/python3.11 \
  -m unittest discover -s test/2026-11-16_定时任务 -p 'test_*.py'

# 或逐个文件（同样可用）
cd test/2026-11-16_定时任务
/opt/homebrew/opt/python@3.11/bin/python3.11 test_clock.py
```

## 覆盖

- `test_clock.py`（46）：六种规则、IANA 时区换算、DST 不存在/重复当地时间、月末与 `last`、
  cron 通配/列表/范围/步长、DOM/DOW 同时受限按 OR、cron 合法性与闰日、interval 固定锚点网格、
  once 不重复、`next_dates` 上限、时区与 naive 时间点拒绝。
- `test_schema.py`（26）：严格 YAML（重复键、别名、非字符串键、根类型、畸形、256KB）、
  YAML 1.2 布尔语义（`off/on/yes/no` 为字符串、仅 `true/false` 为布尔）、JSON 输入、未知字段、
  名称/指令边界、会话与目标模式、custom 配置组合、goal/policies/notifications、id/revision、
  interval 锚点继承、dump/parse 往返、文档示例可直接验证。
- `test_store.py`（41）：版本 CAS（put/enable）、owner 隔离（plan/run/manual）、due 认领唯一键与快照、
  grace 边界、missed skip/latest 只补最近一次、once 自动停止、暂停/删除只影响 queued 的 run、
  recover 中断标记、manual `request_id` 幂等、并发认领唯一性（6 线程）。
- `test_service.py`（19）：busy `skip`（标记 `previous_occurrence_running` 且不调用 start）、
  busy `merge`（只保留最新 queued，旧 queued 记 `merged_into_latest`，上一轮结束后自动启动最新）、
  无 active 多 queued + merge 同轮只启动最新/旧 run 同轮 skip、受理后 store 更新失败 cancel 已受理 task、
  observe 永久性 Permission/Key/Value 失效 cancel+interrupted、stop 时背景仍 active 报 stopping、
  adapter 抛 `BusyError` 与受理异常、stop queued / stop running（先置 `stop_requested` 并 cancel，
  观测到终态后记 `stopped/user_stopped`）与参数校验、审批等待超时（到点置 `stopping/approval_timeout`、
  取消后终态 `failed/approval_timeout`，恢复运行时清空 `waiting_since`）、通知 available/disabled/失败、
  `start()` 触发 recover、单实例所有权锁与释放后可重新获取。
- `test_runtime_adapter.py`（35）：真实 config/工作区解析/ConversationManager/主任务门闸 + mock 任务受理。
  - snapshot：new 继承默认值、custom 全量覆盖、goal start 强制 execute/auto_approval、existing 不冻结、
    `SchedulerService.submit` 落库快照与同配置覆盖复用旧快照、existing 提交无快照。
  - 身份：host admin 字段、host 模式校验、未知用户、API 用户跨工作区、未知 host 工作区、
    普通用户工作区不存在、普通用户 direct/全网络拒绝、跨工作区对话、跨用户对话、Chat 对话拒绝。
  - 门闸：new 建对话并写入 title/schedule_id/run_id/metadata、token 随 `InternalDirectives` 移交且不提前释放；
    existing 复用对话并落盘 custom 设置（pending_* 清空）；goal 参数透传；门闸被占时先失败且不调用受理；
    受理异常时释放门闸。
  - 状态：task 不存在→interrupted；goal active/paused；pending approval→waiting；后台工作→running；
    notice 续接取最新 task_id；成功取最近 assistant 文本摘要；failed/stopped 映射。
  - 取消：按 username/task_id 调用 cancel；无 task_id 不调用。
- `test_notifications.py`（12）：`pending_notifications` 只挑终态 pending、available 认领并发 token、
  2 分钟内不重领、超时重领并作废旧 token、ack 的 user/run/token 隔离、失败原因记录、
  单批最多 10 条、token 字段不允许经 `update_run` 写入。
- `test_schedules_api.py`（19）：自建最小 Flask app + 真实 `schedules_bp` + 真实 `attach_security_hooks`，
  `get_service`/adapter 注入替身，覆盖认证/CSRF/主机 Bearer、options、submit/validate/list/actions/yaml/runs、
  用户隔离与通知 claim/ack（详见下节）。
- `test_schedule_tools.py`（16）：`modules/scheduled_tasks/tools.py` 的身份、只读/计划模式与权限上限；
  owner 使用真实 getter 方法，执行端用 FakeExecutionBackend / Mock 注入 kind（详见下节）。
- `test_imports.py`（1）：临时 cwd 子进程导入 clock/schema/store，确认未加载 `config`。

### HTTP API（server/schedules.py）

- 认证：未登录 GET 401；`/schedules` 页面未登录 302 → `/login`；已登录会话 + 无效 Bearer
  不回退 Cookie（401）；host Bearer 只在 host 模式 + 回环 + token 匹配时生效，且身份不落 Cookie、免 CSRF。
  测试只把 `browser_session_valid()` 替换为 True 以跳过 host 密码状态，`is_logged_in` 的
  username + login_nonce 池校验仍走真实实现。
- CSRF 有限注明：`attach_security_hooks` 的 `before_request` 先于视图认证，因此**未登录 POST**
  在没有有效 CSRF 头时先返回 403（`CSRF validation failed`）而不是 401；只有会话内有 CSRF token
  但未登录才返回 401。带正确 `X-CSRF-Token` 的已登录 POST 正常放行；Bearer 通道按中间件规则免 CSRF。
- `options`：只读，断言不调用 `get_service`、不调用 `get_execution_backend`（不创建终端）；
  普通用户返回 default 工作区、模型（剔除 disabled）、时区与 `host_admin=false`；host admin 用工作区目录。
- submit/validate：definition 与 YAML 两条输入、未知字段/overwrite 类型/非对象 JSON/超过 300000 字节、
  无效 YAML/definition；validate 只校验且 `next_dates` 不回写（store 仍空）；overwrite 需要 id+revision
  匹配（旧 revision 409，重复 ID 不覆盖 409）。
- list / detail / yaml / runs / actions：工作区与 enabled 筛选；导出 YAML 可回解析；actions
  enable/pause/delete/run/stop 与 revision CAS、缺失 run_id 400、未知动作/未知字段 400、不存在 404；
  软删除后 detail 404 但 runs 历史仍可查。
- 用户隔离：另一用户列表为空，detail/yaml/runs/actions 全 404；同 ID 在不同用户间互不覆盖。
- claim/ack：claim 返回 token/name/workspace_id；重复 claim 不再发放；错误 token、跨用户、二次 ack
  返回 `acknowledged=false`；缺少 token 400、error 非字符串 400、错误原因截断 1000 字；GET 405。

### Agent 工具（modules/scheduled_tasks/tools.py）

- Schema：三个工具名、`intent`+必填项、manage 动作枚举、`additionalProperties=false`，工具参数不含身份字段。
- 身份：`_principal` 由终端推导（host admin / API 用户 / 普通用户），模型参数里的 `username` 不被采信；
  跨用户查询/管理均 KeyError，列表为空。
- 只读/计划模式：不能 submit、run、enable、pause、delete、stop；query 仍可用（docs 要求）。
- `_guard` 权限上限（真实 getter + 替身 adapter 设置）：remote 执行端先于身份校验拒绝；计划
  permission/execution/network 任一高于当前终端即 PermissionError；等于或更低允许；终端网络为 None
  时按 restricted 处理（full 计划拒绝）。direct 计划 + sandbox 终端端到端提交失败且不落库。
- 文件来源：`submit_schedule` 通过执行端 `files.read_file` 读取（本地为 FakeExecutionBackend 真实契约），
  缺失/坏 YAML 不落库；overwrite 需已存在 ID（不存在抛 KeyError），revision 旧值 ConflictError。

## 已确认契约

- interval 首个触发是 `anchor + interval`，锚点本身不单独触发（未来锚点同样如此）。
- YAML 采用 1.2 布尔语义：`off/on/yes/no` 是普通字符串，只有 `true/false` 解析为布尔；
  `docs/scheduled_tasks.md` 的示例可直接通过验证。

## 当前结果

`Ran 215 tests ... OK`（215/215 通过）。执行时实现 hash：clock `062f007b`、schema `f3d09238`、
store `874875d9`、service `b5d9385a`、runtime `dcf45c2d`、tools `0d99a968`、server/schedules `8494abfd`。

临时目录断言统一用 `Path(...).resolve()` 比较：macOS 的 `tempfile` 可能给出 `/var/folders/...`，
而 config 会规范化为 `/private/var/folders/...`（`/var` 是符号链接），直接比字符串会在部分沙箱中误报。

### 已修复缺陷（回归锁定）

早期 runtime `3909c490` 上两条探针失败，暴露“显式 direct 被静默降级”问题：

1. `IdentityTest.test_regular_user_direct_execution_is_rejected_not_silently_downgraded`
2. `IdentityTest.test_host_admin_explicit_direct_with_readonly_permission_is_not_silently_downgraded`

证据：`custom.execution_mode=direct`（schema 允许）经 `_settings` 末尾
`settings.update(resolve_conversation_modes(...))` 被静默改成 `sandbox`，既不报错也不触发
`validate_creation_settings` 的 direct 权限校验；`service.submit` 返回成功，DB 中 definition 仍为
`direct`、`principal.configuration_snapshot.execution_mode=sandbox`。这与 docs
“字段、模型能力和权限组合必须经过既有规则校验，不静默降级或扩大权限”冲突。
主侧已按 A 方案修复（runtime `2f4d8ebb`），探针与全量用例现均通过；保留这两条测试防止回归。

## 观察（非失败断言）

- 未知 host 工作区报 `ValueError("工作区不存在")`（不会回退到其他工作区）。
- `recover()` 只把已开始状态标为 `interrupted`，`queued` 保持原状。
- 软删除后的计划 ID 成为墓碑，`put(overwrite=True)` 不能复用。
- 通知投递重领窗口固定 2 分钟、单批上限 10 条，均由 store 查询决定。
- busy 的 `skip/merge` 在 `_start` 内实现；同计划已有 active run 或 adapter 抛 `BusyError` 走同一分支。
- runtime adapter 的 observe 终态字面量是 `success`（runtime.py 把 run 记录的 `succeeded` 映射为 `success`）；
  替身 adapter 若返回 `succeeded` 不会被 service 当成终态（本次测试即按契约改用 `success`）。
- CSRF 中间件先于认证执行（见上节），这是现有全局中间件顺序，不是 schedules 蓝图自身逻辑。
- `server/schedules.py` 的 `submit` 同时收到 `yaml` 与 `definition` 时静默优先 `yaml`（未报错）。
- `tools._guard` 返回 adapter.validate 的结果（已校验 principal），不是 definition；`manage_schedule`
  若缺少 `action` 会以 KeyError 结束（Schema 已要求，正常调用不会触发）。
