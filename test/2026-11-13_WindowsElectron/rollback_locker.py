"""Hold one synthetic installer-test file open without delete sharing."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
allowed = {
    (root / "output/windows-electron/installer-test/installed-app/AA-rollback-guard.txt").resolve(),
    (root / "output/windows-electron/nsis-probe/installed/zz-locked.txt").resolve(),
}
if len(sys.argv) != 2 or Path(sys.argv[1]).resolve() not in allowed:
    raise ValueError("Only an exact synthetic rollback marker may be locked")
expected = Path(sys.argv[1]).resolve()
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                              wintypes.HANDLE]
kernel.CreateFileW.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
handle = kernel.CreateFileW(str(expected), 0x80000000, 0, None, 3, 0, None)
if handle == ctypes.c_void_p(-1).value:
    raise ctypes.WinError(ctypes.get_last_error())
try:
    print("READY", flush=True)
    sys.stdin.buffer.read(1)
finally:
    kernel.CloseHandle(handle)
