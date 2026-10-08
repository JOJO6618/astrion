#!/usr/bin/env python3
"""Enable/reset Host web password: python3 scripts/host_password.py --password '...'."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="设置或重设 Host 网页密码，并开启密码保护。")
    parser.add_argument("--password", required=True, help="要设置的密码，8 至 1024 个字符")
    parser.add_argument("--data-dir", help="显式指定 DATA_DIR；默认与当前服务配置一致")
    args = parser.parse_args(argv)
    # Support a standalone source-script invocation from any working directory.
    if __package__ in {None, ""}:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from config import DATA_DIR, TERMINAL_SANDBOX_MODE
    from modules.host_auth import HostAuthError, host_auth_path, set_host_password

    if (TERMINAL_SANDBOX_MODE or "").lower() != "host":
        parser.exit(1, "当前配置不是 Host 模式，请先选择 Host 模式。\n")
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else Path(DATA_DIR)
    try:
        set_host_password(data_dir, args.password)
    except (HostAuthError, OSError):
        # Do not print exceptions which may contain user-supplied values.
        parser.exit(1, "设置失败：密码须为 8 至 1024 个字符，且数据目录必须可写。\n")
    print(f"Host 网页密码保护已开启：{host_auth_path(data_dir)}")
    print("旧网页登录会话已失效；桌面端和本机 CLI Bearer 认证不受影响。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
