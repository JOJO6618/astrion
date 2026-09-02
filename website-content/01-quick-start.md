# 快速上手

本章带你从零跑起 Astrion：克隆代码、完成初始化配置、启动服务，并根据你的使用场景选择正确的运行形态。

---

## 1. 系统要求

| 依赖 | 要求 | 说明 |
|------|------|------|
| Python | 3.9 及以上（推荐 3.11） | 后端运行环境 |
| Node.js | 18 及以上 | 构建前端、使用 CLI |
| Docker | 可选 | 仅 **web/docker 模式**需要，host 模式不需要 |
| WSL2 | 仅 Windows | Windows 上使用宿主机沙箱的前置条件 |

macOS、Linux、Windows（WSL2）均可运行。三平台沙箱能力的差异见《核心概念》一章。

---

## 2. 安装

```bash
# 1. 克隆仓库
git clone https://github.com/JOJO6618/astrion.git
cd astrion

# 2. 初始化：创建 venv、安装依赖、运行交互式配置向导
#    向导会依次询问：运行模式 → 监听地址/端口 → 管理员账户 → 模型 API → 生成密钥
./setup.sh

# 3. 构建前端
npm install && npm run build

# 4.（仅 web/docker 模式）构建沙箱镜像，见下文「选择运行模式」
docker build -f docker/terminal.Dockerfile -t my-agent-shell:latest .

# 5. 启动
./start.sh
# 或者手动启动：
python -m server.app --port 8091 --thinking-mode
```

启动后访问 `http://localhost:8091`，用向导中设置的管理员账户登录。

> `setup.sh` 向导会把配置写入仓库根目录的 `.env`。这是开发备用方式；生产部署建议改用「数据根目录下的 `settings.json`」或系统环境变量，见下文「配置优先级」。

---

## 3. 运行端口与监听地址

- **默认端口：`8091`**（`WEB_SERVER_PORT`）。
- 监听地址（`WEB_SERVER_HOST`）：
  - 单机自用：建议 `127.0.0.1`，只监听本机回环，局域网不可达；
  - 多用户/服务器部署：用 `0.0.0.0`。
- 启动命令行参数 `--port` 可以临时覆盖配置。
- 调试模式 `WEB_SERVER_DEBUG=1`（同时开启 Flask reloader），生产环境保持 `0`。

---

## 4. 数据路径：数据都存在哪、怎么搬迁

Astrion 的运行态数据（对话记录、用户、日志、部署级配置）**默认全部放在用户主目录下，不污染源码树**：

```
~/.astrion/astrion/           ← 数据根目录（data_root）
├── settings.json             ← 唯一配置文件（优先级最高）
├── config/                   ← 部署级配置（模型库等，host/web 共享）
├── host/                     ← host 模式的数据
│   ├── data/  users/  logs/  api/
└── web/                      ← web/docker 模式的数据（结构同上）
```

按运行模式自动分流：`TERMINAL_SANDBOX_MODE=host` 时数据进 `host/`，否则进 `web/`。

### 路径相关环境变量（优先级从高到低）

| 环境变量 | 作用 |
|----------|------|
| `DATA_DIR` / `LOGS_DIR` / `USER_SPACE_DIR` / `API_USER_SPACE_DIR` | 单独覆盖某一个目录（最高优先级） |
| `ASTRION_DATA_ROOT` | 整体搬迁数据根目录（默认 `~/.astrion/astrion`） |
| `DEPLOY_CONFIG_DIR` | 单独搬迁部署级配置目录（默认 `<数据根>/config/`） |

具体目录变量支持相对路径（相对仓库根目录展开）、绝对路径与 `~`。

### 配置优先级

同名配置生效顺序：**`<数据根>/settings.json` ＞ 系统环境变量 ＞ 仓库根目录 `.env` ＞ 代码默认值**。

`.env` 加载时不覆盖已存在的系统环境变量；`settings.json` 是推荐的生产配置方式，例如：

```json
{
  "server": { "port": 8091, "host": "127.0.0.1" }
}
```

---

## 5. 配置主智能体模型

主智能体的模型统一在 **`custom_models.json`** 中注册。

**放置位置**（按回退链查找，找到即止）：

1. `<数据根>/config/custom_models.json`（生产推荐）
2. 仓库内 `config/custom_models.json`
3. 仓库内 `config/custom_models.json.example`（种子示例）

**完整字段说明**：

```json
{
  "models": [
    {
      "model_name": "Kimi-K3",
      "description": "展示给用户的模型描述",
      "visible": true,
      "url": "${API_BASE_KIMI}",
      "apikey": "${API_KEY_KIMI}",
      "multimodal": "image,video",
      "reasoning_capability": "fast,thinking",
      "reasoning_effort": true,
      "context_window": 1048576,
      "max_output_tokens": 64000,
      "thinkmode_status": {
        "type": "param_toggle",
        "model_id": "k3",
        "fast_extra_parameter":     { "thinking": { "type": "disabled" } },
        "thinking_extra_parameter": { "thinking": { "effort": "max" } }
      },
      "extra_parameter": {},
      "model_description": "注入系统提示词的模型自我描述"
    }
  ]
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `model_name` | ✅ | 模型条目名，UI 中显示，也是各配置引用它的 key |
| `url` | ✅ | API 基础地址。**支持 `${环境变量}` 引用**，密钥不要写明文 |
| `apikey` | ✅ | API Key，同样支持 `${...}` |
| `description` | | 列表中展示的说明文字 |
| `visible` | | 是否在模型选择菜单中可见 |
| `multimodal` | | `image,video` 等；决定输入栏是否允许发图/视频 |
| `reasoning_capability` | | `fast,thinking`；决定思考模式可选项 |
| `reasoning_effort` | | 是否支持「推理强度」滑块 |
| `context_window` | | 上下文窗口大小（token），压缩阈值、用量统计以此为基准 |
| `max_output_tokens` | | 单次最大输出 token |
| `thinkmode_status` | | `param_toggle` 类型：`model_id` 为真实模型 ID；`fast/thinking_extra_parameter` 为两种模式下分别附加的请求参数 |
| `extra_parameter` | | 所有请求都附加的额外参数 |
| `model_description` | | 注入系统提示词的自我介绍 |

**最小配置**只需要 4 个字段：`model_name` / `url` / `apikey` / `thinkmode_status.model_id`，其余字段均有默认值。

> 提醒：`setup.sh` 向导在第 5 步创建的模型条目就是最小配置——`multimodal` 为 `none`（不能发图/视频）、`context_window` 固定 128000、`max_output_tokens` 32768。如果你的模型支持多模态或更大上下文，配完向导后请手动编辑 `<数据根>/config/custom_models.json` 补齐字段。

**默认模型**：不设 `AGENT_DEFAULT_MODEL` 时使用列表中第一个可见模型；也可以在个人空间「模型与思考」页设置每用户默认模型。

> 注意：旧版的 `AGENT_API_*` / `AGENT_THINKING_*` / `AGENT_TITLE_*` 环境变量已从代码中移除，配置它们不再有任何效果。

---

## 6. 配置子智能体模型

子智能体（含三个审核智能体）使用**独立的模型库**：`sub_agent_models.json`。

**放置位置**：仅从部署配置目录读取——`<数据根>/config/sub_agent_models.json`（可用 `SUB_AGENT_MODELS_CONFIG_FILE` 单独覆盖）。**没有这个文件，子智能体将无法启动**，会直接报「未找到可用子智能体模型配置」。

**结构**：

```json
{
  "default_model": "deepseek-v4-flash",
  "models": [
    {
      "name": "deepseek-v4-flash",
      "url": "${SUB_AGENT_API_BASE}",
      "apikey": "${SUB_AGENT_API_KEY}",
      "model_id": "deepseek-v4-flash",
      "modes": "fast,thinking",
      "multimodal": "image",
      "max_output": 32000,
      "max_context": 128000,
      "extra_parameter": {},
      "fast_extra_parameter": {},
      "thinking_extra_parameter": {}
    }
  ]
}
```

要点：

- `default_model`：默认条目名；子智能体/审核智能体配置里 `model` 留空时用它。若指定的名字不存在，回退到列表中第一个可用条目。
- 字段名做了宽松兼容：`name`/`model_name`/`model`、`url`/`base_url`、`apikey`/`api_key` 均可；`modes` 写 `fast,thinking` 表示支持思考模式，只写 `fast` 表示纯快速模式。
- 也支持与主模型库相同的 `thinkmode_status` 结构，可以直接从 `custom_models.json` 复制条目改名字用。
- `url` / `apikey` 同样支持 `${环境变量}` 引用。

**子智能体相关环境变量**：

| 变量 | 默认 | 说明 |
|------|------|------|
| `SUB_AGENT_MAX_ACTIVE` | 5 | 同时运行的子智能体上限 |
| `SUB_AGENT_DEFAULT_TIMEOUT` | 180（秒） | 默认超时 |
| `SUB_AGENT_TASKS_BASE_DIR` | `<数据根>/<模式>/data/sub_agent_tasks` | 任务目录 |
| `SUB_AGENT_PROJECT_RESULTS_DIR` | `<工作区>/sub_agent_results` | 交付目录（有意放在工作区内） |

---

## 7. 选择运行模式：host 还是 docker（web）

由 `TERMINAL_SANDBOX_MODE` 决定，两种模式面向完全不同的场景：

| | **host 模式** | **web/docker 模式** |
|---|---|---|
| 定位 | 本地个人使用 | 服务器多用户部署 |
| 命令执行 | 宿主机 OS 沙箱（macOS sandbox-exec / Linux bwrap / Windows WSL2） | 每个用户独立 Docker 容器，受限档权限下以非特权 uid 执行 |
| 数据目录 | `~/.astrion/astrion/host/` | `~/.astrion/astrion/web/` |
| 前置准备 | 无需镜像；Windows 需先装 WSL2 | 必须先构建镜像：`docker build -f docker/terminal.Dockerfile -t my-agent-shell:latest .`（构建上下文必须是仓库根） |
| 镜像名 | — | 由 `TERMINAL_SANDBOX_IMAGE` 指定，建议 `my-agent-shell:latest` |

选择建议：

- **一个人在自己电脑上用** → `host`。文件管理器、本地工具链直接可用，体验最好。
- **部署到服务器给多人用** → `docker`。容器天然隔离用户之间的文件与进程。
- host 模式可以同时读取 web 模式的数据（用户列表、工作区合并显示），反向不行。

沙箱镜像基于 `python:3.11-slim`，内置 LibreOffice / Pandoc / FFmpeg / Tesseract OCR（含中文）/ Chromium / Node 20 / docx / pptxgenjs 等常用工具链。**程序不会自动构建镜像**，web 模式启动前必须手动构建一次。

---

## 8. 首次启动检查清单

1. 浏览器打开 `http://localhost:8091`，用管理员账户登录；
2. 进入个人空间，确认「模型与思考」页能看到你配置的模型；
3. 随便发一条消息，确认主智能体能正常回复；
4. 让主智能体创建一个子智能体（或直接说「帮我查一下 xxx」触发），确认 `sub_agent_models.json` 配置正确；
5. 若是 web 模式，确认镜像已构建、终端容器能正常拉起。

到这里，你的 Astrion 已经可以正常工作了。接下来建议阅读《核心概念》，理解部署模式、权限模式、执行环境、运行模式这四组概念——它们决定了 Astrion 的行为边界。
