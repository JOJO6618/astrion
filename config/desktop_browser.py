"""桌面内置浏览器 AI 凭据：macOS 经 fd 3，Windows 经一次性 stdin 管道。

必须在任何子进程启动前读取：管道读完即关，Windows stdin 恢复为空设备，
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
        if fd == 0 and os.name == "nt":
            # Keep a valid STD_INPUT_HANDLE for later subprocess/terminal creation.
            # Replacing fd 0 closes the credential pipe without leaving a stale handle.
            import ctypes
            from ctypes import wintypes
            import msvcrt

            null_fd = os.open(os.devnull, os.O_RDONLY)
            try:
                os.dup2(null_fd, 0, inheritable=False)
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
                kernel.SetStdHandle.restype = wintypes.BOOL
                if not kernel.SetStdHandle(-10 & 0xFFFFFFFF, msvcrt.get_osfhandle(0)):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                if null_fd != 0:
                    os.close(null_fd)
        else:
            try:
                os.close(fd)
            except OSError:
                pass
    token = b"".join(chunks).decode("ascii", "ignore").strip()
    return token if 32 <= len(token) <= 256 else ""


_TOKEN = _read_token()


def desktop_browser_token() -> str:
    return _TOKEN
