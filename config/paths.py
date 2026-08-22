"""项目路径与目录配置。

运行态数据存放在 ``~/.astrion/astrion/`` 下：

::

    ~/.astrion/astrion/            ← data_root（默认，可通过 ASTRION_DATA_ROOT 覆盖）
    ├── settings.json              ← 唯一配置文件
    ├── config/                    ← 部署级 JSON，host/web 模式共享
    ├── host/                      ← host 模式（单用户）
    │   ├── data/                  ← 对话/记忆/数据库
    │   └── logs/
    └── web/                       ← web 模式（多用户）
        ├── users/                 ← 每用户独立工作区
        │   └── <name>/data/
        └── logs/

路径解析优先级（从高到低）：

1. 具体目录环境变量（``DATA_DIR`` / ``LOGS_DIR`` / ``USER_SPACE_DIR`` /
   ``API_USER_SPACE_DIR``）——单独覆盖某个目录，最高优先级；
2. ``ASTRION_DATA_ROOT`` ——整体搬迁数据根目录；
3. 兜底默认值 ``~/.astrion/astrion``。

注意：``config/*.json`` 分两类，不再一概锚定源码树：

- **开发者配置 / 程序能力**（``docker_risk_markers.json`` / ``skill_hints.json``）：
  是程序行为的一部分，随版本演进，仍锚定源码树。
- **部署者自定义配置**（``custom_models`` / ``host_workspaces`` /
  ``auto_approval`` / ``goal_review`` / ``forbidden_commands`` /
  ``host_sandbox_policy``）：因部署/机器而异、或含密钥，外置到
  ``<data_root>/config/``（即 ``DEPLOY_CONFIG_DIR``）。读取走
  ``resolve_deploy_config``，回退链为：部署目录 -> 源码树 ``.json`` ->
  源码树 ``.json.example``。
"""

import json
import os
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _norm(path: Path) -> Path:
    """展开 ``~``、转绝对路径并归一化。相对路径相对仓库根目录展开。"""
    path = path.expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return path.resolve()


def _env(name: str) -> str:
    return str(os.environ.get(name, "") or "").strip()


def _resolve_repo_path(raw_value: str, default: str) -> str:
    """解析锚定在源码树的路径（配置文件、提示词等）。

    相对路径相对仓库根目录展开，绝对路径与 ``~`` 原样支持。
    """
    candidate = str(raw_value or "").strip() or str(default)
    return str(_norm(Path(candidate)))


def _runtime_mode() -> str:
    """运行模式：``host`` 或 ``web``。

    仅当 ``TERMINAL_SANDBOX_MODE=host`` 时为 host 模式。
    优先从 settings.json 读取，其次环境变量，最后默认 host。
    """
    # 尝试从已加载的 settings.json 中读取
    mode = _env("TERMINAL_SANDBOX_MODE").lower()
    if mode == "host":
        return "host"
    if mode == "web" or mode == "docker":
        return "web"
    return "host"


# ── 数据根目录（data_root）──
# 优先级：ASTRION_DATA_ROOT 环境变量 > 默认 ~/.astrion/astrion
_DEFAULT_DATA_ROOT = Path("~/.astrion/astrion")
RUNTIME_ROOT = str(_norm(Path(_env("ASTRION_DATA_ROOT") or _DEFAULT_DATA_ROOT)))

_MODE = _runtime_mode()


def _runtime_dir(env_name: str, subdir: str) -> str:
    """解析运行态子目录。

    优先级：具体目录环境变量 > data_root/{mode}/subdir。
    具体目录变量支持相对路径（相对仓库根目录）、绝对路径与 ``~``。
    """
    raw = _env(env_name)
    if raw:
        return str(_norm(Path(raw)))
    return str(Path(RUNTIME_ROOT) / _MODE / subdir)


# ── 运行态目录（默认落在 <data_root>/<mode>/ 下）──
DATA_DIR = _runtime_dir("DATA_DIR", "data")
LOGS_DIR = _runtime_dir("LOGS_DIR", "logs")
USER_SPACE_DIR = _runtime_dir("USER_SPACE_DIR", "users")
# API 专用用户与工作区（与网页用户隔离）
API_USER_SPACE_DIR = _runtime_dir("API_USER_SPACE_DIR", "api/users")

# ── 用户自定义 skill 归档目录（host 模式全局；docker/web 模式可与 per-user 并存）──
CUSTOM_SKILLS_DIR = str(Path(RUNTIME_ROOT) / _MODE / "agentskills")

# ── 是否宿主机模式 ──
IS_HOST_MODE = _MODE == "host"

# ── 固定 web 模式路径（host 模式下作为额外数据源，同时读取）──
_WEB_ROOT = Path(RUNTIME_ROOT) / "web"
WEB_DATA_DIR = str(_WEB_ROOT / "data")
WEB_USER_SPACE_DIR = str(_WEB_ROOT / "users")

# ── 部署级配置目录 —— 放在 data_root 根下，两个模式共享 ──
_DEFAULT_DEPLOY_CONFIG = Path(RUNTIME_ROOT) / "config"
DEPLOY_CONFIG_DIR = str(_norm(Path(_env("DEPLOY_CONFIG_DIR") or _DEFAULT_DEPLOY_CONFIG)))
# 源码树内的配置种子目录（首次运行/开发环境的默认内容来源）。
_SEED_CONFIG_DIR = _REPO_ROOT / "config"


def deploy_config_path(filename: str) -> str:
    """部署级配置文件应写入/读取的位置（``<data_root>/config`` 下）。

    用于会被运行时写回的文件（如 host_workspaces / host_sandbox_policy），
    始终指向部署目录，不回退源码树。
    """
    return str(Path(DEPLOY_CONFIG_DIR) / filename)


def resolve_deploy_config(filename: str) -> str:
    """解析部署级配置的只读路径，回退链（高→低）：

    1. ``<data_root>/config/<filename>``（部署者真实配置）
    2. 源码树 ``config/<filename>``（仓库内种子）
    3. 源码树 ``config/<filename>.example``（示例兜底）

    都不存在时返回部署目录下的目标路径，由调用方处理「文件不存在」。
    仅用于定位，不在此做拷贝（拷贝交给 setup / 迁移脚本）。
    """
    deployed = Path(DEPLOY_CONFIG_DIR) / filename
    if deployed.exists():
        return str(deployed)
    seed = _SEED_CONFIG_DIR / filename
    if seed.exists():
        return str(seed)
    example = _SEED_CONFIG_DIR / f"{filename}.example"
    if example.exists():
        return str(example)
    return str(deployed)


# ── 启动工作区 ──
DEFAULT_PROJECT_PATH = _resolve_repo_path(os.environ.get("DEFAULT_PROJECT_PATH", ""), "./project")
HOST_PROJECT_PATH = _resolve_repo_path(os.environ.get("HOST_PROJECT_PATH", ""), DEFAULT_PROJECT_PATH)

# ── 源码树内的配置与资源（不随运行态根目录迁移）──
PROMPTS_DIR = _resolve_repo_path(os.environ.get("PROMPTS_DIR", ""), "./prompts")
AGENT_SKILLS_DIR = _resolve_repo_path(os.environ.get("AGENT_SKILLS_DIR", ""), "./agentskills")
# 多智能体预设角色目录（源码树内，随版本分发）
PRESET_ROLES_DIR = _resolve_repo_path(os.environ.get("PRESET_ROLES_DIR", ""), "./multi_agent_roles")
# 运行态预设/自定义角色目录（host 模式：预设+自定义同目录；web 模式：仅预设）
CUSTOM_ROLES_DIR = str(Path(RUNTIME_ROOT) / _MODE / "mutiagents" / "agents")
# web 模式预设角色目录（web 模式下使用，host 模式下不用）
WEB_PRESET_ROLES_DIR = str(Path(RUNTIME_ROOT) / "web" / "mutiagents" / "agents")
WORKSPACE_SKILLS_DIRNAME = ".astrion/skills"
WORKSPACE_MEMORY_DIRNAME = ".astrion/memory"
WORKSPACE_REVIEW_DIRNAME = ".astrion/review"
HOST_WORKSPACES_FILE = _resolve_repo_path(
    os.environ.get("HOST_WORKSPACES_FILE", ""),
    deploy_config_path("host_workspaces.json"),
)

# ── 基于 DATA_DIR 派生的数据文件 ──
USERS_DB_FILE = f"{DATA_DIR}/users.json"
INVITE_CODES_FILE = f"{DATA_DIR}/invite_codes.json"
ADMIN_POLICY_FILE = f"{DATA_DIR}/admin_policy.json"
API_USERS_DB_FILE = f"{DATA_DIR}/api_users.json"
API_TOKENS_FILE = f"{DATA_DIR}/api_tokens.json"
API_USAGE_FILE = f"{DATA_DIR}/api_usage.json"

__all__ = [
    "RUNTIME_ROOT",
    "DEFAULT_PROJECT_PATH",
    "HOST_WORKSPACES_FILE",
    "HOST_PROJECT_PATH",
    "PROMPTS_DIR",
    "DATA_DIR",
    "LOGS_DIR",
    "AGENT_SKILLS_DIR",
    "WORKSPACE_SKILLS_DIRNAME",
    "WORKSPACE_MEMORY_DIRNAME",
    "WORKSPACE_REVIEW_DIRNAME",
    "IS_HOST_MODE",
    "USER_SPACE_DIR",
    "WEB_DATA_DIR",
    "WEB_USER_SPACE_DIR",
    "USERS_DB_FILE",
    "INVITE_CODES_FILE",
    "ADMIN_POLICY_FILE",
    "API_USER_SPACE_DIR",
    "CUSTOM_SKILLS_DIR",
    "PRESET_ROLES_DIR",
    "CUSTOM_ROLES_DIR",
    "WEB_PRESET_ROLES_DIR",
    "API_USERS_DB_FILE",
    "API_TOKENS_FILE",
    "API_USAGE_FILE",
    "DEPLOY_CONFIG_DIR",
    "deploy_config_path",
    "resolve_deploy_config",
]
