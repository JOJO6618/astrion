# 私有项目常驻记忆回归（Host）

## 离线验证

```bash
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B -m unittest discover -s 'test/2026-11-07_私有项目记忆' -p 'test_*.py' -v
ASTRION_IGNORE_DOTENV=1 .venv/bin/python -B -m unittest discover -s 'test/2026-11-07_私有项目记忆' -p 'test_*.py'
```

使用合成临时目录，不读取真实 `.env`、记忆、账号或凭据，不启动服务和模型请求。18项用例覆盖：

- Host 工作区路径隔离与自动授权、路径摘要防同名碰撞。
- 新建启用、老对话恢复不补注入、空文件快照、450行全文不截断。
- 普通请求与恢复不读磁盘、压缩失效后重新读取。
- 快照持久化一起提交，保存失败不冻结。
- 已注入的唯一文件免预读，普通文件仍守门，readonly 不放行。
- 普通 write/edit 文件后端、跨对话旧版本写入冲突与合并重试。
- 子创建与压缩正文、恢复冻结提示词、其余系统段保持不变。
- 新 Chat 不加入 Agent 项目记忆。
- Host 记忆目录位于工作区 data_dir，不在代码仓库内，也不创建容器专用 `.gitignore`。
- 外部后端明确拒绝，不回读控制端；记忆目录符号链接拒绝。
- 绑定遗留 Docker 会话的 owner 在记忆位置与主题目录处明确报错，不会被当作 Host 路径。

容器挂载、容器内文件代理与重启后挂载持久化属于 Docker fork 的验收范围，本目录不再包含这些用例。

## 已执行的相关存量回归

```bash
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B -m unittest discover -s 'test/2026-11-07_公共执行层' -p 'test_tool_dispatch.py'
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B -m unittest discover -s 'test/2026-11-07_公共执行层' -p 'test_files.py'
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B -m unittest discover -s 'test/2026-10-22_子智能体统一' -p 'test_*.py'
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B -m unittest discover -s 'test/2026-10-07_子智能体权限与单次完全访问' -p 'test_scoped_files.py'
```

分别19、14、23、13项通过。公共创建测试的最小 parent 夹具已补齐真实 FileManager、项目根与 host session，以覆盖创建时的新增记忆加载。

## 有限原生验收

```bash
ASTRION_IGNORE_DOTENV=1 /opt/homebrew/bin/python3.11 -B 'test/2026-11-07_私有项目记忆/native_check.py'
```

- macOS：在宿主启动项目自身的 Seatbelt profile；实测可写 profile 写入记忆成功，readonly profile 写入失败且文件保持原内容。外层沙箱禁止嵌套 sandbox-exec，因此该有限脚本通过单次完全访问执行成功。
- Docker 原生挂载、容器重建与容器内 Git 忽略行为改由 Docker fork 验收，本仓不再提供 `native_check.py docker` 入口。
- Linux／Windows 原生运行与真实模型使用仍未验收；本轮没有前端改动，无需前端构建。
