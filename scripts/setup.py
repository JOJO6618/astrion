#!/usr/bin/env python3
"""首次启动初始化向导（终端交互）。

在启动 Web 服务之前，于终端一次性设置好"必须在程序启动前确定"的内容，
并写出项目根目录的 ``.env``：

- 运行模式 host / web（决定数据目录与子容器策略，在 import config 时即锁定）
- Web 监听端口 / 地址（服务 listen 前必须确定）
- 管理员账号（用户名 + 密码哈希，与 user_manager._ensure_admin_user 兼容）
- 会话与 Token 密钥（自动随机生成，不打扰用户）
- 基础 API 三件套（base_url / key / model_id）
- 可选的 thinking / title 模型

设计要点：

* **单一线性流程**：逐项询问，每项带默认值，回车即取默认。
* **不联网校验**：API 配置只写入，不当场测试连通性。
* **以 .env.example 为模板**：逐行读取模板，仅替换被本向导接管的键的值，
  其余行（包括全部注释与未涉及的配置项）原样保留——保证生成的 .env
  仍带完整文档，也不会丢任何配置开关。
* **不 import config**：向导在 .env 尚不存在时运行，且模式选择会影响路径解析，
  因此刻意不依赖 config，避免过早锁定路径或触发 .env 加载。

用法：
    python -m scripts.setup           # 交互式向导
    python scripts/setup.py           # 同上
    python -m scripts.setup --force   # 跳过"已存在 .env"的确认（仍会备份）
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
import time
from getpass import getpass
from pathlib import Path

try:
    from werkzeug.security import generate_password_hash
except ImportError:  # pragma: no cover - 缺依赖时给出可读提示
    print("缺少依赖 werkzeug，请先安装：pip install -r requirements.txt")
    sys.exit(1)


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"


def _deploy_config_dir(mode: str, custom_data: str) -> Path:
    """推导部署级配置目录（~/.astrion/astrion/config）。

    必须与 config/paths.py 的解析保持一致：
    - 自定义数据根（向导里填的）优先，作为运行态根目录；
    - 否则默认 ~/.astrion/astrion；
    - 配置目录 = 运行态根 / config。

    setup 刻意不 import config（避免在 .env 写好前过早锁定路径），
    因此这里用向导现场收集到的 mode / custom_data 自行推导。
    """
    root = Path(custom_data).expanduser() if custom_data else (Path("~/.astrion").expanduser() / "astrion")
    return (root / "config").resolve()


# === 终端交互辅助 ===========================================================

def _ask(prompt: str, default: str = "") -> str:
    """询问一项，回车取默认。default 非空时在提示里展示。"""
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        raw = ""
    return raw or default


def _ask_choice(prompt: str, choices: list[str], default: str) -> str:
    """从有限选项中选择，输入不在集合内则重问。"""
    choices_lower = {c.lower(): c for c in choices}
    hint = "/".join(choices)
    while True:
        raw = _ask(f"{prompt}（{hint}）", default).lower()
        if raw in choices_lower:
            return choices_lower[raw]
        print(f"  请输入 {hint} 之一。")


def _ask_required(prompt: str, default: str = "") -> str:
    """必填项，留空则重问。"""
    while True:
        value = _ask(prompt, default)
        if value:
            return value
        print("  此项必填，不能为空。")


def _ask_password(prompt: str) -> str:
    """隐藏输入密码，二次确认一致。"""
    while True:
        first = getpass(f"{prompt}: ")
        if not first:
            print("  密码不能为空。")
            continue
        second = getpass("再次输入确认: ")
        if first != second:
            print("  两次输入不一致，请重试。")
            continue
        return first


# === .env 生成 =============================================================

def _render_env(values: dict[str, str]) -> str:
    """以 .env.example 为模板渲染 .env：仅替换被接管键的值，其余原样保留。

    被接管的键写入用户配置值；模板里未被接管的键与全部注释、空行原样保留。
    若某接管键在模板中不存在（理论上不会），追加到末尾。
    """
    if not ENV_EXAMPLE_PATH.exists():
        # 没有模板时退化为按 values 直接生成（无注释）
        return "".join(f"{k}={v}\n" for k, v in values.items())

    remaining = dict(values)
    out_lines: list[str] = []
    for raw in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out_lines.append(f"{key}={remaining.pop(key)}")
                continue
        out_lines.append(raw)

    # 模板里没有、但本向导要写的键（兜底，正常不会触发）
    if remaining:
        out_lines.append("")
        out_lines.append("# === 由 setup 向导补充 ===")
        for key, value in remaining.items():
            out_lines.append(f"{key}={value}")

    return "\n".join(out_lines) + "\n"


def _backup_existing_env() -> Path | None:
    if not ENV_PATH.exists():
        return None
    backup = ENV_PATH.with_name(f".env.bak_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(ENV_PATH, backup)
    return backup


# === custom_models.json 生成 ==============================================

def _build_custom_model_entry(
    *, model_name: str, url: str, api_key: str, model_id: str, supports_thinking: bool
) -> dict:
    """构造一个最小可用的 custom_models 条目。

    字段与 config/model_profiles.py 的解析规则对齐：
    - url / apikey 直接写字面量值（解析器对非全大写字符串按字面量处理）；
    - param_toggle 类型用 thinkmode_status.model_id 承载真实模型 ID；
    - reasoning_capability 决定是否暴露思考能力。
    """
    capability = "fast,thinking" if supports_thinking else "fast"
    entry: dict = {
        "model_name": model_name,
        "description": model_name,
        "visible": True,
        "url": url,
        "apikey": api_key,
        "multimodal": "none",
        "reasoning_capability": capability,
        "context_window": 128000,
        "max_output_tokens": 32768,
        "thinkmode_status": {
            "type": "param_toggle",
            "model_id": model_id,
            "fast_extra_parameter": {},
            "thinking_extra_parameter": {},
        },
        "extra_parameter": {},
        "model_description": f"你的基础模型是 {model_name}。",
    }
    return entry


def _write_custom_models(target_path: Path, entry: dict) -> Path | None:
    """写出 custom_models.json 到部署配置目录。已存在则先备份再覆盖。"""
    backup = None
    if target_path.exists():
        backup = target_path.with_name(
            f"custom_models.json.bak_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(target_path, backup)
    payload = {"models": [entry]}
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return backup


# === 主流程 ================================================================

def run_wizard(force: bool = False) -> int:
    print("=" * 60)
    print("  Astrion 首次启动初始化向导")
    print("=" * 60)
    print("逐项设置启动前必须确定的内容，每项可回车使用 [默认值]。")
    print()

    if ENV_PATH.exists() and not force:
        print(f"检测到已存在配置文件：{ENV_PATH}")
        ans = _ask_choice("是否备份后重新配置", ["y", "n"], "n")
        if ans == "n":
            print("已取消，未做任何修改。")
            return 0

    values: dict[str, str] = {}

    # --- 1. 运行模式 ---
    print("\n[1/5] 运行模式")
    print("  host = 单机本地模式，命令直接在宿主机沙箱执行")
    print("  web  = 多用户模式，每个用户分配独立 Docker 子容器")
    mode = _ask_choice("选择运行模式", ["host", "web"], "host")
    values["TERMINAL_SANDBOX_MODE"] = mode
    default_host = "127.0.0.1" if mode == "host" else "0.0.0.0"

    # --- 2. Web 服务 ---
    print("\n[2/5] Web 服务监听")
    values["WEB_SERVER_PORT"] = _ask("监听端口", "8091")
    print("  监听地址：单机本地建议 127.0.0.1（只听本机）；多用户/服务器用 0.0.0.0")
    values["WEB_SERVER_HOST"] = _ask("监听地址", default_host)

    # --- 3. 数据目录（可选覆盖）---
    print("\n[3/5] 数据目录")
    default_data = "~/.astrion/astrion"
    print(f"  运行态数据（对话/用户/记忆/日志）默认存放在 {default_data}")
    custom_data = _ask("自定义数据根目录（留空用默认）", "")
    if custom_data:
        # 数据根变量：ASTRION_DATA_ROOT
        values["ASTRION_DATA_ROOT"] = custom_data

    # --- 4. 管理员账户 ---
    print("\n[4/5] 管理员账户")
    admin_user = _ask("管理员用户名", "admin")
    admin_pw = _ask_password("管理员密码")
    values["AGENT_ADMIN_USERNAME"] = admin_user
    values["AGENT_ADMIN_PASSWORD_HASH"] = generate_password_hash(admin_pw)

    # --- 5. 模型配置（写入部署级配置目录 ~/.astrion/astrion/config/custom_models.json）---
    print("\n[5/5] 模型配置")
    print("  配置一个可用模型（写入 ~/.astrion/astrion/config/custom_models.json）。")
    print("  启动后还可在后台页面继续增删模型。")
    model_name = _ask_required("模型显示名（界面上看到的名字）", "默认模型")
    model_url = _ask_required("API 地址（Base URL）", "")
    model_apikey = _ask_required("API Key", "")
    model_id = _ask_required("模型 ID（请求里实际发送的 model 字段）", "")
    supports_thinking = _ask_choice(
        "该模型是否支持思考模式", ["y", "n"], "n"
    ) == "y"

    model_entry = _build_custom_model_entry(
        model_name=model_name,
        url=model_url,
        api_key=model_apikey,
        model_id=model_id,
        supports_thinking=supports_thinking,
    )

    # --- 自动生成密钥 ---
    values["WEB_SECRET_KEY"] = secrets.token_hex(32)
    values["API_TOKEN_SECRET"] = secrets.token_hex(32)

    # --- 写出 ---
    rendered = _render_env(values)
    backup = _backup_existing_env()
    ENV_PATH.write_text(rendered, encoding="utf-8")
    try:
        ENV_PATH.chmod(0o600)  # .env 含密钥，收紧权限（Windows 上静默忽略）
    except (OSError, NotImplementedError):
        pass
    # 模型配置写到部署级配置目录（~/.astrion/astrion/config），而非源码树
    custom_models_path = _deploy_config_dir(mode, custom_data) / "custom_models.json"
    models_backup = _write_custom_models(custom_models_path, model_entry)

    # --- 摘要 ---
    print("\n" + "=" * 60)
    print("  配置完成")
    print("=" * 60)
    print(f"  配置文件：{ENV_PATH}")
    if backup:
        print(f"  原配置已备份：{backup}")
    print(f"  模型配置：{custom_models_path}")
    if models_backup:
        print(f"  原模型配置已备份：{models_backup}")
    print(f"  运行模式：{mode}")
    print(f"  监听地址：{values['WEB_SERVER_HOST']}:{values['WEB_SERVER_PORT']}")
    print(f"  数据目录：{custom_data or default_data}")
    print(f"  管理员：  {admin_user}")
    print(f"  模型：    {model_name}（{model_id}）@ {model_url}")
    print(f"  密钥：    已自动生成 WEB_SECRET_KEY / API_TOKEN_SECRET")
    print()
    print("  下一步启动服务：")
    print("    python -m server.app")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Astrion 首次启动初始化向导")
    parser.add_argument(
        "--force",
        action="store_true",
        help="跳过『已存在 .env』的确认提示（仍会先备份再覆盖）",
    )
    args = parser.parse_args()
    try:
        return run_wizard(force=args.force)
    except KeyboardInterrupt:
        print("\n已中断，未写入配置。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
