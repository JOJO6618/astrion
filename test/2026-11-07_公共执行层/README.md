# 公共执行层回归与跨进程实验

契约：`docs/execution_contract.md`。本次实际结果：`VALIDATION.md`。

本目录包含文件、命令、终端、后台、权限、上下文、媒体、浅版本和真实工具分派的离线回归，以及独立进程 stdio 可行性探针。它不是生产 Runner，也不提供公网协议。

## 运行全部 unittest 回归

需要已安装项目依赖的 Python；本机使用 `/opt/homebrew/bin/python3.11`。

```bash
ASTRION_IGNORE_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 \
/opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s test/2026-11-07_公共执行层 -p 'test_*.py'
```

各组使用临时数据、合成配置和替身，不应读取真实 `.env`、凭据或对话，不启动 Astrion 服务。普通工具本身被 macOS 沙箱包裹时，不能嵌套 Seatbelt 或向某些子进程发送信号，实际执行项会明确跳过；这不能当作原生执行通过。

## 原生 macOS 执行与管道收口

在允许嵌套 Seatbelt、发送自身子进程信号的宿主执行环境运行：

```bash
ASTRION_IGNORE_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 \
/opt/homebrew/bin/python3.11 -B test/2026-11-07_公共执行层/test_local.py

ASTRION_IGNORE_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 \
/opt/homebrew/bin/python3.11 -B test/2026-11-07_公共执行层/test_background_cleanup.py

ASTRION_IGNORE_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 \
/opt/homebrew/bin/python3.11 -B test/2026-11-07_公共执行层/test_process_cleanup.py
```

智能体从沙箱发起这些验证时，须走平台的单次完全访问流程，不能通过改沙箱配置或白名单凑通过。测试仅结束自身创建的进程，不终止已运行的 Astrion 或其他应用。

`test_local.py` 验证真实前台与后台 PID 取消、只读拒写、可写命令、working_dir 和终端生命周期。cwd 使用显式标记行比较，保留 shell 的 stderr，不要求整份混合输出恰好等于路径。

## 用例分工

| 文件 | 主要验证 |
| --- | --- |
| `test_files.py`、`test_binary_files.py` | 真实 FileManager 薄适配、文本／字节、路径与授权 |
| `test_container_metadata.py` | 容器脚本元数据与拒绝语义；离线执行真实脚本，不启动 Docker |
| `test_local.py`、`test_import_isolation.py` | 独立资源装配、不导入模型／Flask 栈、真实本机生命周期 |
| `test_background_cleanup.py`、`test_process_cleanup.py` | 后台／终端进程、reader 与自有管道收口 |
| `test_container_environment.py` | 容器工作区、大小写规则与完全访问能力不借宿主事实 |
| `test_fake.py` | 纯内存协议、后台通知及终端替身 |
| `test_read_routing.py`、`test_environment_routing.py` | 上下文文件、技能、树、环境来源，未知字段不冒充控制端 |
| `test_scope_routing.py` | 子实例独立资源与完全访问 owner 身份 |
| `test_media_routing.py` | 媒体／OCR 经所选文件资源 |
| `test_server_routing*.py` | 后台 REST、审批、通知回滚、计划、浅版本恢复 |
| `test_tool_dispatch.py` | 真实 handle_tool_call；控制端资源被设为访问即抛错，防止假通过 |
| `test_versioning_backend.py` | 执行侧字节快照、恢复／删除与控制端备份分工 |

测试从具体分类目录 discover；不新增 `__init__.py`，不依赖根目录递归收集。

## 跨进程 stdio 探针

```bash
ASTRION_IGNORE_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 \
/opt/homebrew/bin/python3.11 -B test/2026-11-07_公共执行层/test_transport.py
```

`test_transport.py` 自带测试主程序，须单独运行；unittest discover 不会执行这 13 个探针场景。`probe_transport.py` 是执行侧，`transport_harness.py`／`probe_transport_io.py` 处理测试传输。

测试刻意让三个目录不同：

```text
临时根/workspace  执行侧工作区
临时根/data       执行侧资源数据
临时根/control    控制侧及 probe 的启动 cwd
```

文件和命令产物只能落在 workspace。协议日志与 stdout 帧分流，解析坏帧后连接仍可继续。

### 实验帧约定

- 一行一个 UTF-8 JSON 对象；请求含 `jsonrpc=2.0`、int/string id、白名单 method、对象 params；响应原样回显 id。
- ready 为无 id 主动消息；拒绝 NaN／Infinity。
- bytes 显式编码 `{encoding: "base64", data: "...", size: N}`，不猜测二进制格式。
- 方法为 dict 白名单，不使用 eval 或任意 getattr。

| 方法 | 行为 |
| --- | --- |
| `env.info`、`ping`、`shutdown` | 握手、存活、退出 |
| `fs.write_text`、`fs.read_text`、`fs.read_binary` | 文本／二进制 |
| `fs.read_segment`、`fs.search`、`fs.extract_segments` | 行段／搜索／抽取 |
| `fs.inspect` | 授权后元数据 |
| `exec.run`、`cancel` | 前台命令与请求取消 |

错误码：解析 -32700，非法请求 -32600，未知方法 -32601，参数 -32602，处理异常 -32603，后端不可用 -32010，接口缺方法 -32011。

探针在受限工具沙箱中检测到不能嵌套 Seatbelt 后，会明确使用 direct **探针模式**测试命令传输，仍受外层工具沙箱约束。此时只能证明执行位置与取消响应，不证明 OS 隔离或 PID 消失；真实 PID 与 Seatbelt 以原生 `test_local.py` 为准。

### 未覆盖范围

stdio 不覆盖后台／终端的跨传输链路、网络连接、配对、E2EE、断线恢复、幂等、背压、并发乱序或崩溃恢复。后台和终端的本地实现由其余回归验证，不把这些本地结果当作网络协议验收。

Linux 受限助手、Windows WSL2 与真实 Docker 本轮没有实机验收。替身目标环境文本和离线容器脚本验证不能替代平台运行测试。
