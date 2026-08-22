---
name: terminal-guide
description: 持久化终端使用指南。使用 terminal_session、terminal_input、terminal_snapshot 工具前必须阅读此技能。用于理解持久化终端与 run_command（含后台模式）的分工、output_wait 参数含义、以及会话型任务的处理方式。
---

# 持久化终端使用指南

## 核心原则

**持久化终端 = 会话型命令行工具（重点是“会话状态”，不是“单纯时长”）**

Terminal 工具提供了一个可以持续存在的终端会话，命令在其中运行不会因为你去做其他事情而中断。

### 与 run_command 的本质区别

| 特性 | terminal 系列 | run_command |
|------|--------------|-------------|
| 会话类型 | 持久会话，可复用上下文 | 一次性执行 |
| 超时行为 | output_wait 到期后命令继续运行 | timeout 到期强制终止命令 |
| 适用场景 | 会话状态依赖、多步串行、常驻服务 | 单步命令、快速查询、长任务后台化 |
| 状态检查 | 可随时 snapshot 查看会话内容 | 通过 command_id + sleep(wait_runcommand_id) 等待 |
| 典型用途 | npm run dev、tail -f、先 cd/activate 再连续执行 | npm run build、pip/brew/apt 安装、ls/file/grep |

**选择标准（新）**
- 单条命令，即使很长（构建/安装）→ **优先 run_command 后台模式**
- 需要 shell 会话状态（`cd`/`export`/`source venv` 后继续）→ **terminal**
- 需要常驻服务或持续日志跟踪（`npm run dev`/`tail -f`）→ **terminal**
- 只是快速查看信息 → **run_command 前台**

## 何时使用

### 适合 Terminal 的场景

**会话上下文依赖任务：**
- `cd` 到目录后连续执行多条命令
- 激活虚拟环境后连续执行（`source venv/bin/activate`）
- 设置环境变量后再执行后续命令

**需要保持运行的服务：**
- 开发服务器（npm run dev、python -m http.server）
- 数据库服务（redis-server、mongod）
- 监控工具（tail -f、watch）

**需要持续查看同一会话输出：**
- 长时间跟踪日志
- 观察服务状态
- 多步执行中随时 snapshot

### 不适合 Terminal 的场景（优先 run_command）

**快速信息查看：**
- 查看文件信息（file、stat、ls -la）
- 检查编码（iconv -l、file -i）
- 简单的 grep 定位

**单条长命令（建议后台模式）：**
- `npm run build`
- `pip install -r requirements.txt`
- `brew install xxx` / `apt install xxx`
- 单次 `git clone` / 单次打包构建

**交互式程序（禁止使用）：**
- Python/Node REPL
- vim、nano、emacs
- top、htop
- 任何需要完整 TTY 的程序

# 简单的交互程序 如只需要输入输出内容的py,node程序等可以使用

## 三步工作流

使用 terminal 工具的标准流程：

### 1. 打开会话

```python
terminal_session(
    action="open",
    session_name="main",  # 给会话起个名字
    working_dir="project"  # 可选：指定工作目录
)
```

**会话命名建议：**
- `main` - 主要工作终端
- `server` - 运行开发服务器
- `build` - 执行构建任务
- `test` - 运行测试

### 2. 发送命令

```python
terminal_input(
    command="pip install -r requirements.txt ; echo '__PipInstall_DONE__'",
    session_name="main",
    output_wait=30  # 收集输出的窗口期（秒）
)
```

### 3. 检查状态

```python
terminal_snapshot(
    session_name="main",
    lines=50,  # 可选：返回最近 50 行
    max_chars=5000  # 可选：限制字符数
)
```

## output_wait 的艺术

**output_wait 不是命令超时！** 这是最容易误解的地方。

### 它是什么

`output_wait` 是"收集输出的窗口期"：
- 在这个时间内，工具会等待并收集命令的输出
- 窗口期结束后，**命令继续在后台运行**，只是不再等待输出
- 如果命令在窗口期内完成，会提前返回结果

### 它不是什么

- ❌ 不是命令超时（命令不会被终止）
- ❌ 不是最大执行时间（命令可以运行更久）
- ❌ 不是强制等待时间（命令完成会提前返回）

### 设置策略

**场景 1：快速命令，只需确认启动**
```python
# 例如：启动开发服务器
terminal_input(
    command="npm run dev",
    output_wait=10  # 10秒足够看到启动信息
)
# 服务器会继续运行，你可以去做其他事情
```

**场景 2：长任务，只需确认开始**
```python
# 例如：下载大文件
terminal_input(
    command="wget https://example.com/large-file.zip ; echo '__Download_DONE__'",
    output_wait=30  # 30秒确认下载开始
)
# 下载继续进行，稍后用 snapshot 检查进度
```

**场景 3：必须等待完成的任务**
```python
# 例如：运行测试套件（预计 2 分钟完成）
terminal_input(
    command="pytest tests/ ; echo '__Pytest_DONE__'",
    output_wait=150  # 设置足够长，确保完成
)
```

**场景 4：可能长时间无输出的任务**
```python
# 例如：git clone 大仓库（可能长时间无输出）
# 技巧：在命令后加“便于识别的后缀标记”作为完成信号
terminal_input(
    command="git clone https://github.com/large/repo.git ; echo '__GitClone_DONE__'",
    output_wait=30  # 30秒确认开始
)
# 稍后用 snapshot 检查，看到 __GitClone_DONE__ 就知道完成了

# 例如：构建任务
terminal_input(
    command="npm run build ; echo '__Build_DONE__'",
    output_wait=60
)

# 例如：安装依赖
terminal_input(
    command="python3 -m pip install requests ; echo '__Pip_DONE__'",
    output_wait=60
)
```

### 经验值参考

- **确认启动**：5-10 秒
- **确认开始**：20-30 秒
- **短任务完成**：30-60 秒
- **中等任务完成**：60-120 秒
- **长任务完成**：120-300 秒

**原则：宁可设长，不要设短。** 设短了可能看不到关键输出，设长了最多就是等一会儿。

## 常见场景示例

### 场景 1：启动开发服务器（启动后不需要等待）

```python
# 1. 打开终端
terminal_session(action="open", session_name="server")

# 2. 启动服务器
terminal_input(
    command="npm run dev",
    session_name="server",
    output_wait=15  # 15秒看到启动信息即可
)
# 输出：Server running on http://localhost:3000

# 3. 服务器在后台运行，你可以继续做其他事情
# 需要时可以随时查看日志
terminal_snapshot(session_name="server", lines=30)
```

### 场景 2：下载大文件（启动后定期检查）

```python
# 1. 打开终端
terminal_session(action="open", session_name="download")

# 2. 启动下载（加完成标记）
terminal_input(
    command="wget https://example.com/dataset.tar.gz ; echo '__Download_DONE__'",
    session_name="download",
    output_wait=20  # 20秒确认下载开始
)

# 3. 去做其他事情...

# 4. 稍后检查进度
terminal_snapshot(session_name="download")
# 输出：50% [======>       ] 512MB  10.2MB/s  eta 45s

# 5. 再次检查
terminal_snapshot(session_name="download")
# 输出：100% [=============] 1024MB  10.5MB/s  in 98s
#       __Download_DONE__  ← 看到这个就知道完成了
```

### 场景 3：运行测试套件（需要等待完成）

```python
# 1. 打开终端
terminal_session(action="open", session_name="test", working_dir="backend")

# 2. 运行测试（预计 2 分钟）
terminal_input(
    command="pytest tests/ -v ; echo '__Pytest_DONE__'",
    session_name="test",
    output_wait=150  # 设置足够长，等待完成
)
# 输出：完整的测试结果

# 3. 如果不确定是否完成，再检查一次
terminal_snapshot(session_name="test", lines=20)
```

### 场景 4：批量处理文件（需要等待完成）

```python
# 1. 打开终端
terminal_session(action="open", session_name="process")

# 2. 批量转换（预计 5 分钟）
terminal_input(
    command="for img in *.png; do convert \"$img\" -resize 800x600 \"thumb_$img\"; done ; echo '__Convert_DONE__'",
    session_name="process",
    output_wait=300  # 5分钟
)

# 3. 检查是否完成
terminal_snapshot(session_name="process")
```

### 场景 5：多步操作（cd + 虚拟环境 + 命令）

```python
# 1. 打开终端
terminal_session(action="open", session_name="main")

# 2. 切换目录
terminal_input(
    command="cd backend && source venv/bin/activate",
    session_name="main",
    output_wait=5
)

# 3. 在虚拟环境中安装依赖
terminal_input(
    command="pip install -r requirements.txt ; echo '__PipInstall_DONE__'",
    session_name="main",
    output_wait=60
)

# 4. 运行应用
terminal_input(
    command="python app.py",
    session_name="main",
    output_wait=10
)
```

## 常见陷阱

### 1. 把 output_wait 当作命令超时

❌ **错误理解：**
```python
# "我想让命令最多运行 30 秒，超时就终止"
terminal_input(command="long_task.sh", output_wait=30)
```

✅ **正确做法：**
```python
# 如果需要强制超时终止，使用 run_command
run_command(command="long_task.sh", timeout=30)

# 如果任务可以持续运行，使用 terminal
terminal_input(command="long_task.sh", output_wait=30)
# 30秒后命令继续运行，你可以稍后用 snapshot 检查
```

### 2. 不检查命令是否完成就继续输入

❌ **错误：**
```python
terminal_input(command="npm install", output_wait=10)
# 10秒后立即执行下一个命令，但 npm install 可能还在运行
terminal_input(command="npm run build", output_wait=10)
# 可能会冲突或失败
```

✅ **正确：**
```python
terminal_input(command="npm install ; echo '__NpmInstall_DONE__'", output_wait=10)

# 检查是否完成
terminal_snapshot(session_name="main")
# 如果看到还在运行，等待或增加 output_wait

# 确认完成后再继续（也可加完成标记）
terminal_input(command="npm run build ; echo '__Build_DONE__'", output_wait=60)
```

### 3. 在终端中启动交互式程序

❌ **错误：**
```python
terminal_input(command="python", output_wait=5)  # 启动 Python REPL
terminal_input(command="print('hello')", output_wait=5)  # 不会工作
```

❌ **错误：**
```python
terminal_input(command="vim file.txt", output_wait=5)  # vim 需要完整 TTY
```

✅ **正确：**
```python
# 使用 run_command 探测并调用合适的 Python 解释器
run_command(command="python3 -c 'print(\"hello\")'", timeout=5)

# 使用 edit_file 修改文件
edit_file(file_path="file.txt", old_string="...", new_string="...")
```

### 4. 对快速命令使用 terminal

❌ **低效：**
```python
terminal_session(action="open", session_name="check")
terminal_input(command="ls -la", session_name="check", output_wait=5)
terminal_session(action="close", session_name="check")
```

✅ **高效：**
```python
run_command(command="ls -la", timeout=5)
```

### 5. 忘记关闭不再使用的终端

```python
# 任务完成后，记得关闭终端释放资源
terminal_session(action="close", session_name="build")
```

或者重置终端（清空输出但保持会话）：
```python
terminal_session(action="reset", session_name="main")
```

### 6. output_wait 设置过短导致看不到关键输出

❌ **问题：**
```python
# pip install 可能需要 30-60 秒
terminal_input(command="pip install tensorflow ; echo '__PipTensorflow_DONE__'", output_wait=5)
# 5秒后返回，只看到 "Collecting tensorflow..."，看不到安装结果
```

✅ **改进：**
```python
# 方案 1：设置足够长的 output_wait，并加完成标记
terminal_input(command="pip install tensorflow ; echo '__PipTensorflow_DONE__'", output_wait=90)

# 方案 2：短 output_wait 确认开始，稍后用 snapshot 检查完成标记
terminal_input(command="pip install tensorflow ; echo '__PipTensorflow_DONE__'", output_wait=10)
# ... 做其他事情 ...
terminal_snapshot(session_name="main")  # 检查是否完成
```

## 检查命令是否完成

使用 `terminal_snapshot` 判断命令状态的技巧：

### 技巧 1：使用完成标记（推荐）

对于可能长时间无输出的任务，在命令后加“便于识别的后缀标记”（推荐格式：`__<Task>_DONE__`）：

```python
# ✅ 好的做法（有清晰后缀）
terminal_input(
    command="git clone https://github.com/large/repo.git ; echo '__GitClone_DONE__'",
    output_wait=30
)
terminal_input(
    command="npm run build ; echo '__Build_DONE__'",
    output_wait=60
)
terminal_input(
    command="python3 -m pip install requests ; echo '__Pip_DONE__'",
    output_wait=60
)

# 稍后检查
terminal_snapshot(session_name="main")
# 看到对应的 __XXX_DONE__ 就知道完成了
```

```python
# ❌ 不好的做法（不加后缀，难以快速判断完成）
terminal_input(
    command="git clone https://github.com/large/repo.git",
    output_wait=30
)
terminal_input(
    command="npm run build",
    output_wait=60
)
terminal_input(
    command="python3 -m pip install requests",
    output_wait=60
)
```

**为什么有用：**
- git clone、wget、scp 等命令可能长时间无输出
- 完成后也可能没有明显的提示信息
- 自定义后缀（如 `__Build_DONE__`）是明确完成信号，不会误判

### 技巧 2：看提示符

```bash
# 命令还在运行（没有提示符）
Downloading... 45%

# 命令已完成（出现提示符）
Downloading... 100%
user@host:~/project$
```

### 技巧 3：看输出内容

```bash
# 安装完成的标志
Successfully installed package-1.0.0

# 测试完成的标志
===== 10 passed in 2.5s =====

# 构建完成的标志
Build completed successfully

# 下载完成的标志
100% [=============] 1024MB
```

### 技巧 4：看进程状态

```python
# 如果不确定，可以检查进程
terminal_input(command="ps aux | grep your_process", output_wait=5)
```

## 总结

**Terminal 工具的本质：** 持久化会话，让命令可以长期运行，随时查看状态。

**核心工作流：**
1. `terminal_session(action="open")` - 打开会话
2. `terminal_input(command="...", output_wait=N)` - 发送命令
3. `terminal_snapshot()` - 检查状态

**关键理解：**
- `output_wait` 是收集输出的窗口期，不是命令超时
- 命令会在后台持续运行，即使 output_wait 结束
- 随时可以用 `snapshot` 查看最新状态

**选择标准：**
- 长时间任务、后台服务、多步操作 → terminal
- 快速查看信息、一次性命令 → run_command
- 交互式程序 → 禁止使用，改用专用工具

**最佳实践：**
- output_wait 宁长勿短
- 不确定是否完成时，先用 snapshot 检查
- 任务完成后关闭或重置终端
- 给会话起有意义的名字
