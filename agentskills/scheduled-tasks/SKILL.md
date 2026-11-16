---
name: scheduled-tasks
description: 创建、查询和管理 Astrion 定时 Agent 任务。用于每天、每周、每月、单次、固定间隔或 cron 自动执行，指定工作区、新建或已有对话，调整配置、目标和忙碌/漏跑策略，或查看运行记录。不是操作系统定时器，不支持关机执行和事件触发。
---

# 定时 Agent 任务

计划在 Astrion 后端运行期间执行。桌面关闭窗口仍驻留，显式退出应用后停止；重启按漏跑策略处理。只支持 Agent，不能指定 Chat 对话。

## 流程

1. 从用户的话确定指令、时间、时区、目标工作区和新建/已有对话。缺少时区时使用当前用户明确的本地时区；无法确定的关键时间不能猜。
2. 用 `write_file` 创建 YAML，再用 `submit_schedule(file_path, overwrite=false)` 验证保存。文件只是提交材料，数据库中的配置为权威；改文件不会自动改计划。
3. 用 `query_schedules` 核对 ID、revision、next_dates 和已保存配置，然后报告实际状态。保存成功不等于任务已经执行成功。
4. 更新时先查询，保留明确 `id` 和最新 `revision`，提交 `overwrite=true`。验证或版本冲突失败不会修改旧计划。更新只影响后续运行，已受理运行保留快照。

用户可从对话列表更多菜单复制对话 ID，从工作区管理复制工作区 ID。ID 是定位标识，不是授权凭据。工作区可省略，提交时固定为当前调用工具的工作区。不要发明模型名或工作区 ID；工具不提供模型/工作区枚举，无法从上下文确定时使用继承或询问用户。

```yaml
schema_version: 1
name: 每日巡检
message: 检查项目测试结果，汇报失败原因与需要我处理的事项。
enabled: true
schedule:
  type: daily
  timezone: Asia/Shanghai
  times: ['09:00']
target:
  conversation:
    mode: new
configuration:
  mode: inherit
goal:
  mode: off
policies:
  busy: skip
  missed: skip
  approval_timeout_seconds: 3600
notifications:
  enabled: true
```

## 时间规则

每个规则都填写 IANA `timezone`，例如 `Asia/Shanghai`、`America/New_York`。

- 单次：`type: once`、`date: '2026-12-01'`、`times: ['09:00']`。
- 固定间隔：`type: interval`、`every: 30`、`unit: minutes`（或 hours）。UTC `anchor` 可省略，保存时固定；首个触发是 anchor + interval，不随每次执行漂移。
- 每天：`type: daily`、`times: ['09:00','18:00']`。
- 每周：`type: weekly`、`days: [1,3,5]`（周日为 0，周一为 1）、`times: ['09:00']`。
- 每月：`type: monthly`、`day: last`（也可 1–31）、`times: ['09:00']`。31 日跳过没有该日的月份。
- Cron：`type: cron`、`expression: '0 9 * * 1-5'`。标准五字段，支持数字、星号、列表、范围和步长；日期和星期同时限制时按 OR 匹配。

夏令时不存在的当地时间跳过，重复时间仅执行第一次。所有 `times` 使用 HH:MM，最多 24 个。不要混入不属于该频率的字段。

## 对话与配置

已有对话：`target.conversation: {mode: existing, id: conv_...}`。默认沿用执行时该对话的配置。新对话的默认设置在保存时固定，之后不随用户默认值漂移。

自定义配置可填：

```yaml
configuration:
  mode: custom
  # model_key: 已知的模型标识
  thinking: true
  reasoning_effort: high
  work_mode: execute
  permission_mode: auto_approval
  execution_mode: sandbox
  # network_enabled: true  # 仅宿主管理员可显式设网络权限
  worktree: main           # 或 {branch_name: 已注册工作树分支}
```

已有对话的自定义设置会保存到该对话。plan 必须 readonly + sandbox；readonly 必须 sandbox。已有对话使用工作树需要已启用该功能；不能通过计划改写老对话冻结的工具/系统提示词。完全访问或更高权限不能由低权限对话借计划升级，请用户在定时任务页面设置。

## 目标与策略

`goal.mode`：off 普通任务；start 启动目标；continue 继续已有目标（仅 existing）。可选正整数 `max_turns`、`max_tokens`。目标运行复用既有执行/自动审核规则，无法等待人工决策时明确失败/阻塞，不扩大权限。

`policies.busy`：skip 跳过；merge 只保留最近一次，空闲后运行。`policies.missed`：skip 跳过漏跑；latest 只补最近一次。审批或问答等待上限 1–86400 秒，默认 3600。

## 管理与结果

- `query_schedules(schedule_id?, workspace_id?, enabled?, include_runs?, fields?)`：用户级全局列表、配置或运行记录。fields 可选 definition/next_at/next_dates/created_at/updated_at/last_run/active_run/runs。
- `manage_schedule(schedule_id, action, revision?, run_id?, request_id?)`：enable/pause/delete/run/stop。立即运行使用稳定 request_id 可防重复受理；stop 必须 run_id。
- 暂停和删除不会取消正在运行的任务；停止本轮是独立操作。
- 运行 queued/starting/running/waiting/stopping 与 success/failed/skipped/stopped/interrupted 分开。主回复结束可能仍有后台工作，不能自行宣称成功。
- notification_status 是单独的投递状态，available 不代表已送达。实际成功/失败以运行记录为准。
