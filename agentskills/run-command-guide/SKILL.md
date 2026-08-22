---
name: run-command-guide
description: run_command 工具使用指南。介绍前台与后台模式、后台模式处理长时间命令的标准流程（command_id + sleep(wait_runcommand_id)）、与 terminal 的边界、以及互斥参数约束。
---

# run_command 使用指南

## 核心原则

- `run_command` 是 **一次性执行** 的终端命令，适合查询文件信息（`ls`、`file`、`grep -n`）、触发 CLI 工具、做简单的数据转换或运行非交互脚本。它**不能**用于启动交互式程序（如 `python` REPL、`vim`、`top`）。
- 每次调用必须提供 `timeout`（单位秒，必须大于 0），系统会在超时后强制打断命令并返回当前输出。
- 输出内容有字符数限制（默认 10000 字符），超过时会被截断或遭拒，因此尽量控制命令输出量，必要时拆分多个命令或保存到文件再读取。
- `run_command` 是当前“单条命令”默认入口：短命令走前台，长命令优先走后台模式。
- 只有在需要会话状态（`cd`/`source`/`export` 后多步连续）或常驻服务时，才优先使用 `terminal_*`。

## 长时间任务默认策略（重要）

以下场景默认优先 `run_command(run_in_background=true)`：
- `npm run build` / `cargo build` / `mvn package`
- `pip install ...` / `npm install ...` / `brew install ...` / `apt install ...`
- 单次 `git clone`、单次数据导入/导出、单次压缩打包

标准流程：
1. 后台发起命令并拿到 `command_id`
2. 先继续处理其他任务
3. 需要最终结果时调用 `sleep(wait_runcommand_id=command_id)`
4. 或等待系统完成通知

## 执行模式：前台 vs 后台

### 前台模式（默认，`run_in_background=false`）

- timeout 上限 30 秒。命令在工具完成前会阻塞整个模型线程，返回后即可马上读取 `output`、`return_code` 等字段。
- 适合“快照式”场景：查看目录、检查某个文件、运行一条短命令并立刻需要结果。
- 要求命令在 30 秒内完成，否则会中断并返回 `status: "timeout"`。

### 后台模式（`run_in_background=true`）

- timeout 最长 3600 秒。命令在后台继续执行，工具会等约 5 秒收集当前输出并返回，然后 **立即释放控制权**，让你继续处理其他任务。
- 返回值里会包含 `command_id`，`status` 可能是 `running_background`。你会拿到已经产生的输出片段，但最终结果要靠系统通知或手动等待。
- 成功或失败完成后，系统会自动插入一条 user 消息（前缀 `[系统通知|background_command]`，附带 `command_id` 与输出摘要）通知你。不要依赖任何手动轮询。
- 适合长时间任务（构建/安装/批处理）或你想“先发起再继续别的工作”。
- 返回的 `command_id` 需要保留，用于后续通过 `sleep(wait_runcommand_id=...)` 等待结果或根据通知定位输出。

## 何时不要用 run_command（改用 terminal）

- 需要 `cd` 后连续执行多条命令
- 需要 `source venv/bin/activate` 后持续执行
- 需要常驻服务（如 `npm run dev`）或持续追踪输出（如 `tail -f`）
- 需要长期复用同一 shell 上下文

## 等待后台命令完成：command_id + sleep(wait_runcommand_id)

1. 调用后台命令后记录 `command_id`（形如 `cmd_<时间戳>_<8位hash>`）；
2. 当你确实需要知道最终输出/返回码时，调用 `sleep(wait_runcommand_id=command_id)`；
3. `sleep` 工具会阻塞直到命令完成，返回值的 `result` 字段就是后台命令的最终 payload，`success`/`return_code` 等都可以直接使用；
4. `sleep` 默认会验证该 `command_id` 是否属于当前对话；跨会话调用会报错。

注意：即使你不调用 `sleep`，后台命令完成后也会有系统通知，只要收到消息就可以去交叉检查输出或 `sleep` 结果。

## `sleep` 工具的互斥参数规则

- `sleep` 工具支持三种等待方式：`seconds`（纯等待）、`wait_sub_agent_ids`（等待子智能体）、`wait_runcommand_id`（等待后台 `run_command`）。
- **只能选择其中一个等待参数**，否则会报错 “sleep 的等待参数互斥”；
- 如果你需要短暂停顿而非等待任务完成，传 `seconds` 并可选填 `reason`；
- 需要等待后台命令完成时仅填写 `wait_runcommand_id`，其余两个参数必须为 `null`/不传。
- `wait_runcommand_id` 返回：
  ```
  {
      "success": true,
      "mode": "wait_runcommand_id",
      "command_id": "...",
      "result": {...},  # 与 run_command 结果结构一致
      "message": "后台 run_command 等待完成"
  }
  ```

## 常见坑与注意事项

1. **忘记设置 `timeout` 或给值 ≤ 0**：前台/后台都会立即报错。后台模式还有限制（>0 且 ≤3600）。
2. **误以为后台模式会自动输出最终结果**：后台只返回 `running_background` 的片段，必须通过系统通知或 `sleep(wait_runcommand_id=...)` 才能拿到完整状态。
3. **没有保存 `command_id` 即失去追踪**：`command_id` 是等待、复查、排错的唯一标识符，出错后可直接用它查询后台管理器。
4. **在 wait/run_command 之间同时传多个 `sleep` 参数**：会被客户端拒绝，报错明确指出互斥约束。
5. **命令输出过大（超过 10000 字符）**：会被截断并提示降低输出量，可考虑写入文件再用 `read_file` 读取，或分步执行。
6. **尝试运行交互式程序**：`run_command` 明确禁止，命令会在 `_validate_command` 阶段失败。
7. **后台命令属于别的对话**：`sleep(wait_runcommand_id=...)` 会报 “该后台命令不属于当前对话”，只能在原 conversation 中等待。
8. **误把会话型任务交给 run_command**：当任务依赖 shell 上下文时请切换 `terminal_*`。
9. **误以为 `sleep` 会自动拼接 system message**：`sleep` 只是等待，系统通知仍旧按背景轮询插入，你收到 `system` 消息即可确认状态。

## 示例

### 前台快速查询

```python
run_command(command="ls -a docs | head", timeout=5)
# 立即得到输出和 return_code
```

### 后台安装并等待

```python
# 发起后台安装
bg = run_command(
    command="python -m pip install -r requirements.txt",
    timeout=600,
    run_in_background=True
)
command_id = bg.get("command_id")

# 可先去做别的事情，或立即等待最终状态
sleep(wait_runcommand_id=command_id, reason="等待 pip 安装完成")
# 返回 result["result"]["output"], return_code 等最终内容
```

### 结合系统通知与 sleep

- 系统会插入类似 `后台命令已完成（completed）` 的 `system` 消息，包含 `command_id`。
- 如果你优先希望第一时间处理输出，可以在收到消息后再 `sleep(wait_runcommand_id=command_id)` 来确认成功状态（即使已有通知，也可再次 `sleep` 获得 `result` 结构）。

## 总结

- 单条命令默认使用 run_command：前台用于快照，后台用于长任务。
- 后台结果需要 `command_id + sleep(wait_runcommand_id=...)` 或系统通知来确认最终状态。
- 会话型/常驻型任务请使用 terminal。
- `sleep` 的等待参数互斥，务必只传一个。
- 避免交互式命令、超出字符/超时限制，必要时将输出先写到文件再读。
