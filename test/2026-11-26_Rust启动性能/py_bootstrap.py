#!/usr/bin/env python3.11
"""Python headless 的子进程引导器（仅为基准服务，不改产品代码）。

背景：本沙箱禁止父进程向子进程发送任何信号（SIGTERM/SIGKILL 都是 EPERM），
所以不能让测试脚本主动 kill 后端。这里用“停止文件”让子进程**自己**退出：

1. 父进程 spawn:  python -B this_file.py <stop_file> <module> [module args...]
2. 本文件把 argv 还原成模块参数，用 runpy 以 __main__ 运行目标模块（占住主线程）
3. 后台线程每 20ms 检查 stop_file，一旦出现就 os._exit(0)

目标进程退出不依赖任何信号，也不修改产品代码。
"""
import os
import runpy
import sys
import threading
import time


def watch(path: str) -> None:
    while True:
        if os.path.exists(path):
            os._exit(0)
        time.sleep(0.02)


def main() -> None:
    stop_file = sys.argv[1]
    module = sys.argv[2]
    # 以 cwd（仓库根）为 import 根，和 `python -m server.headless_app` 等价
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    threading.Thread(target=watch, args=(stop_file,), daemon=True).start()
    sys.argv = [module] + sys.argv[3:]
    runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
