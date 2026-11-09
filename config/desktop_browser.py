"""macOS 内置浏览器 AI 端点凭据（桌面壳经 fd 3 管道下发，读完即关）。

必须在任何子进程启动前读取：读完后 fd 被关闭、环境变量被移除，
模型执行的命令与其他子进程都拿不到它。
"""
import os

_TOKEN = ""


def _read_token() -> str:
    raw_fd = os.environ.pop("ASTRION_DESKTOP_BROWSER_TOKEN_FD", "")
    os.environ.pop("ASTRION_DESKTOP_BROWSER_TOKEN", None)
    if not raw_fd.isdigit():
        return ""
    fd = int(raw_fd)
    chunks = []
    try:
        while True:
            data = os.read(fd, 256)
            if not data:
                break
            chunks.append(data)
            if b"\n" in data or sum(len(c) for c in chunks) > 1024:
                break
    except OSError:
        return ""
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    token = b"".join(chunks).decode("ascii", "ignore").strip()
    return token if 32 <= len(token) <= 256 else ""


_TOKEN = _read_token()


def desktop_browser_token() -> str:
    return _TOKEN
