"""多智能体角色存储与解析。

三层结构：

1. **源码树预设**：``multi_agent_roles/``（随版本分发，只读种子）
2. **运行态预设**：
   - host 模式 = ``host/mutiagents/agents/``（同时也是自定义目录）
   - web 模式 = ``web/mutiagents/agents/``
3. **用户自定义**（仅 web 模式按用户隔离）：
   - web 模式 = ``web/users/<user>/personal/mutiagents/agents/``
   - host 模式 = 和预设同目录

启动时调用 `sync_preset_roles()` 把源码树预设同步到 host 和 web 两个运行态预设目录。

**重要**：本模块不再依赖 `IS_HOST_MODE` 判断运行模式。
调用方（API / tools_execution）通过参数显式传入 `runtime_dir` 和 `custom_dir`：
- 不传则仅扫描源码树预设（最小安全回退）
- host 模式：`runtime_dir` 和 `custom_dir` 指向同一目录
- web 模式：`runtime_dir` 指向 web 预设目录，`custom_dir` 指向用户个人目录
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from config.paths import RUNTIME_ROOT, PRESET_ROLES_DIR, CUSTOM_ROLES_DIR, WEB_PRESET_ROLES_DIR
except ImportError:
    RUNTIME_ROOT = str(Path.home() / ".astrion" / "astrion")
    PRESET_ROLES_DIR = ""
    CUSTOM_ROLES_DIR = ""
    WEB_PRESET_ROLES_DIR = ""

from modules.i18n import tr

FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n(?P<rest>.*)$", re.S)

# 角色 ID 白名单：小写字母数字开头，允许连字符/下划线（与现有 ui-operator 等预设兼容）。
# role_id 直接参与文件路径拼接（{role_id}.md），必须拒绝 /、.. 等路径字符。
_ROLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_role_id(role_id: str) -> str:
    """校验并返回合法 role_id；非法时 raise ValueError（防路径穿越写入）。"""
    cleaned = (role_id or "").strip()
    if not _ROLE_ID_RE.match(cleaned):
        raise ValueError(tr("multi_agent_api.role_id_invalid", role_id=repr(cleaned)))
    return cleaned


# ----- 目录推断 -----

def _source_preset_dir() -> Path:
    """源码树预设角色目录（只读种子）。"""
    return Path(PRESET_ROLES_DIR).expanduser().resolve()


def _host_runtime_dir() -> Path:
    """host 模式运行态目录（预设+自定义同目录）。"""
    return Path(CUSTOM_ROLES_DIR).expanduser().resolve()


def _web_runtime_dir() -> Path:
    """web 模式运行态预设目录。"""
    return Path(WEB_PRESET_ROLES_DIR).expanduser().resolve()


def infer_custom_roles_dir(data_dir: str | Path | None) -> Optional[Path]:
    """从工作区 data_dir 推断用户自定义角色目录。

    - data_dir 包含 ``/web/users/`` → web 模式，返回 ``users/<user>/personal/mutiagents/agents``
    - 其他情况 → host 模式，返回 host 运行态目录
    """
    if not data_dir:
        return _host_runtime_dir()
    data_path = str(Path(data_dir).expanduser().resolve())
    # Windows resolve 后路径用反斜杠，统一归一化再匹配
    if "/web/users/" in data_path.replace("\\", "/"):
        # web 模式：按用户隔离
        dp = Path(data_path).resolve()
        # users/<user>/projects/<project_id>/data -> users/<user>/personal/mutiagents/agents
        if dp.name == "data" and dp.parent.parent.name == "projects":
            user_root = dp.parent.parent.parent
            return (user_root / "personal" / "mutiagents" / "agents").resolve()
        # 兼容旧/其他布局
        if dp.name == "data" and dp.parent.parent.name == "project":
            user_root = dp.parent.parent.parent
            return (user_root / "personal" / "mutiagents" / "agents").resolve()
        return (dp.parent / "mutiagents" / "agents").resolve()
    # host 模式
    return _host_runtime_dir()


def _preset_role_ids() -> Set[str]:
    """返回源码树预设角色 id 集合（用于区分自定义角色）。"""
    src = _source_preset_dir()
    if not src.exists():
        return set()
    return {p.stem for p in src.glob("*.md") if p.is_file()}


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ----- 启动同步 -----

def sync_preset_roles() -> None:
    """启动时把源码树预设角色同步到 host 和 web 两个运行态预设目录。

    - 目录不存在则创建并复制全部预设角色。
    - 文件已存在则不覆盖（可能是用户已改过的运行态副本）。
    """
    src = _source_preset_dir()
    if not src.exists() or not src.is_dir():
        return

    preset_files = {p: p.name for p in src.glob("*.md") if p.is_file()}
    if not preset_files:
        return

    rt_root = Path(RUNTIME_ROOT).expanduser().resolve()
    for target_dir in [_host_runtime_dir(), _web_runtime_dir()]:
        target = target_dir.expanduser().resolve()
        # 安全检查：目标必须在运行态根下
        try:
            target.relative_to(rt_root)
        except ValueError:
            continue
        target.mkdir(parents=True, exist_ok=True)
        for src_file, name in preset_files.items():
            dst = target / name
            if not dst.exists():
                shutil.copy2(src_file, dst)


# ----- Frontmatter 解析 -----

def _parse_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw_meta = m.group("body") or ""
    body = m.group("rest") or ""
    meta: Dict[str, Any] = {}
    current_key: Optional[str] = None
    for line in raw_meta.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") or line.startswith("- "):
            value = line.lstrip(" ").lstrip("- ").strip()
            if current_key and isinstance(meta.get(current_key), list):
                meta[current_key].append(value)
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v:
                meta[k] = v
                current_key = None
            else:
                meta[k] = []
                current_key = k
    return meta, body.strip()


# ----- RoleConfig -----

class RoleConfig:
    """一个多智能体角色配置。"""

    __slots__ = (
        "role_id",
        "name",
        "description",
        "body_prompt",
        "model_key",
        "thinking_mode",
        "skills",
        "source_file",
        "is_custom",
    )

    def __init__(
        self,
        *,
        role_id: str,
        name: str,
        description: str = "",
        body_prompt: str = "",
        model_key: Optional[str] = None,
        thinking_mode: str = "fast",
        skills: Optional[List[str]] = None,
        source_file: str = "",
        is_custom: bool = False,
    ):
        self.role_id = role_id
        self.name = name
        self.description = description
        self.body_prompt = body_prompt
        self.model_key = model_key
        self.thinking_mode = thinking_mode or "fast"
        self.skills = list(skills or [])
        self.source_file = source_file
        self.is_custom = is_custom

    def display_name(self, agent_id: int) -> str:
        return f"{self.name}_{agent_id}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role_id": self.role_id,
            "name": self.name,
            "description": self.description,
            "model_key": self.model_key,
            "thinking_mode": self.thinking_mode,
            "skills": self.skills,
            "body_prompt": self.body_prompt,
            "source_file": self.source_file,
            "is_custom": self.is_custom,
        }

    def __repr__(self) -> str:
        return f"<RoleConfig id={self.role_id} name={self.name} custom={self.is_custom}>"


# ----- 文件级加载 -----

def load_role_from_file(file_path: Path, is_custom: bool = False) -> Optional[RoleConfig]:
    if not file_path.exists() or not file_path.is_file():
        return None
    text = file_path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    if not meta.get("id") or not meta.get("name"):
        return None
    return RoleConfig(
        role_id=str(meta["id"]),
        name=str(meta["name"]),
        description=str(meta.get("description") or ""),
        body_prompt=body,
        model_key=(str(meta["model"]) if meta.get("model") else None) or None,
        thinking_mode=str(meta.get("thinking_mode") or "fast"),
        skills=list(meta.get("skills") or []),
        source_file=str(file_path),
        is_custom=is_custom,
    )


# ----- 序列化 -----

def _serialize_role(role: RoleConfig) -> str:
    lines = ["---"]
    lines.append(f"id: {role.role_id}")
    lines.append(f"name: {role.name}")
    if role.description:
        desc = role.description.replace('"', '\\"')
        lines.append(f'description: "{desc}"')
    if role.model_key:
        lines.append(f"model: {role.model_key}")
    lines.append(f"thinking_mode: {role.thinking_mode or 'fast'}")
    if role.skills:
        lines.append("skills:")
        for s in role.skills:
            lines.append(f"  - {s}")
    lines.append("---")
    lines.append("")
    lines.append(role.body_prompt or "")
    return "\n".join(lines)


# ----- 目录级加载 -----

def _scan_roles_dir(dir_path: Path, is_custom: bool = False) -> List[RoleConfig]:
    if not dir_path.exists() or not dir_path.is_dir():
        return []
    roles: List[RoleConfig] = []
    for p in sorted(dir_path.glob("*.md")):
        r = load_role_from_file(p, is_custom=is_custom)
        if r and r.role_id:
            roles.append(r)
    return roles


def _merge_roles(roles_lists: List[List[RoleConfig]]) -> List[RoleConfig]:
    """合并多组角色，后者覆盖前者（同名 role_id 后者优先）。"""
    merged: Dict[str, RoleConfig] = {}
    order: List[str] = []
    for roles in roles_lists:
        for r in roles:
            if r.role_id not in merged:
                order.append(r.role_id)
            merged[r.role_id] = r
    return [merged[rid] for rid in order if rid in merged]


def list_roles(
    runtime_dir: Optional[str | Path] = None,
    custom_dir: Optional[str | Path] = None,
) -> List[RoleConfig]:
    """列出全部角色。

    合并顺序（后者覆盖前者）：
    1. 运行态预设目录
    2. 用户自定义目录（web 模式才有；host 模式与预设同目录，传一个即可）

    ``is_custom`` 标记：不在源码树预设名单中的角色 = 自定义。

    调用方应显式传入 ``custom_dir``：
    - host 模式：传 `_host_runtime_dir()`（或省略，自动回退）
    - web 模式：传入用户个人目录
    """
    # host 模式回退：不传任何目录时使用 host 运行态目录
    if not runtime_dir and not custom_dir:
        runtime_dir = _host_runtime_dir()

    rt_dir = Path(runtime_dir).expanduser().resolve() if runtime_dir else None
    c_dir = Path(custom_dir).expanduser().resolve() if custom_dir else None

    preset_ids = _preset_role_ids()

    # 只有一个目录（host 模式或 web 模式只传了 runtime_dir）
    if not c_dir or (rt_dir and rt_dir == c_dir):
        scan_dir = rt_dir or c_dir
        if scan_dir:
            all_roles = _scan_roles_dir(scan_dir)
        else:
            all_roles = []
        for r in all_roles:
            r.is_custom = r.role_id not in preset_ids
        return all_roles

    # web 模式：合并运行态预设 + 用户自定义
    preset_roles = _scan_roles_dir(rt_dir, is_custom=False) if rt_dir else []
    custom_roles = _scan_roles_dir(c_dir, is_custom=True)
    merged = _merge_roles([preset_roles, custom_roles])
    return merged


def load_preset_role(
    role_id: str,
    runtime_dir: Optional[str | Path] = None,
    custom_dir: Optional[str | Path] = None,
) -> Optional[RoleConfig]:
    """加载一个角色。先查用户自定义目录，再查运行态预设目录。"""
    rt_dir = Path(runtime_dir).expanduser().resolve() if runtime_dir else None
    c_dir = Path(custom_dir).expanduser().resolve() if custom_dir else None
    preset_ids = _preset_role_ids()

    # host 模式回退
    if not rt_dir and not c_dir:
        rt_dir = _host_runtime_dir()

    # 先查自定义目录（web 模式且与 runtime_dir 不同）
    if c_dir and rt_dir and c_dir != rt_dir:
        f = c_dir / f"{role_id}.md"
        r = load_role_from_file(f, is_custom=True)
        if r:
            return r

    # 再查运行态/混合目录
    scan_dir = rt_dir or c_dir
    if scan_dir:
        f = scan_dir / f"{role_id}.md"
        r = load_role_from_file(f)
        if r:
            r.is_custom = r.role_id not in preset_ids
            return r
    return None


def save_custom_role(
    role: RoleConfig,
    custom_dir: Optional[str | Path] = None,
) -> Path:
    """保存角色到自定义目录。不传 custom_dir 时回退到 host 运行态目录。"""
    validate_role_id(role.role_id)
    if custom_dir:
        c_dir = Path(custom_dir).expanduser().resolve()
    else:
        c_dir = _host_runtime_dir()
    _ensure_dir(c_dir)
    f = c_dir / f"{role.role_id}.md"
    f.write_text(_serialize_role(role), encoding="utf-8")
    return f


def delete_custom_role(
    role_id: str,
    custom_dir: Optional[str | Path] = None,
) -> bool:
    """删除用户自定义角色。不传 custom_dir 时回退到 host 运行态目录。"""
    role_id = validate_role_id(role_id)
    if custom_dir:
        c_dir = Path(custom_dir).expanduser().resolve()
    else:
        c_dir = _host_runtime_dir()
    f = c_dir / f"{role_id}.md"
    if not f.exists():
        return False
    f.unlink(missing_ok=True)
    return True


def is_preset_role(role_id: str) -> bool:
    """判断角色是否为预设角色（源码树种子中存在）。"""
    return role_id in _preset_role_ids()


def build_role_system_prompt(role: RoleConfig) -> str:
    """生成子智能体的「专属 prompt」部分（frontmatter 之后的 body）。"""
    return role.body_prompt or ""