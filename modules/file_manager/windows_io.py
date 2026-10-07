"""Windows handle-based IO: pin ancestors and reject reparse points.

Directory handles deny FILE_SHARE_DELETE until the operation finishes, so an
ancestor cannot be renamed or replaced between authorization and file opening.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import stat


class FileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD), ("creation", wintypes.FILETIME),
        ("access", wintypes.FILETIME), ("write", wintypes.FILETIME),
        ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD),
    ]


def _api():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    api.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    return api


def _open(path, access=0x80, disposition=3, directory=False, sharing=3):
    api = _api()
    handle = api.CreateFileW(str(path), access, sharing, None, disposition,
                             0x00200000 | (0x02000000 if directory else 0), None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = FileInformation()
        if not api.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400:
            raise PermissionError("Scoped file IO refuses Windows reparse points")
        if directory != bool(info.attributes & 0x10):
            raise PermissionError("Unexpected Windows file type")
        buffer = ctypes.create_unicode_buffer(32768)
        length = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise PermissionError("Cannot verify Windows file handle identity")
        final = buffer.value
        if final.startswith("\\\\?\\UNC\\"):
            final = "\\\\" + final[8:]
        elif final.startswith("\\\\?\\"):
            final = final[4:]
        if os.path.normcase(os.path.normpath(final)) != os.path.normcase(os.path.normpath(str(path))):
            raise PermissionError("Windows file handle path changed")
        return handle, info
    except BaseException:
        api.CloseHandle(handle)
        raise


@contextmanager
def checked_parent(path, *, create=False, create_roots=None):
    target = Path(path)
    if not target.is_absolute() or ".." in target.parts or target == Path(target.anchor):
        raise PermissionError("Scoped IO requires an absolute non-root path")
    handles = []
    try:
        current = Path(target.anchor)
        handle, _ = _open(current, directory=True)
        handles.append(handle)
        for part in target.parts[1:-1]:
            current = current / part
            try:
                handle, _ = _open(current, directory=True)
            except FileNotFoundError:
                if not create or not any(current == root or root in current.parents for root in (create_roots or [])):
                    raise
                try:
                    os.mkdir(current)
                except FileExistsError:
                    pass
                handle, _ = _open(current, directory=True)
            handles.append(handle)
        yield target
    finally:
        for handle in reversed(handles):
            _api().CloseHandle(handle)


@contextmanager
def open_checked(path, mode="r", encoding="utf-8", create_roots=None):
    import msvcrt
    writing = mode not in {"r", "rb"}
    disposition = 1 if mode == "x" else (4 if writing else 3)
    with checked_parent(path, create=writing, create_roots=create_roots) as target:
        handle, info = _open(target, access=0x40000000 if writing else 0x80000000,
                             disposition=disposition)
        try:
            if info.links != 1 or info.attributes & 0x40:
                raise PermissionError("Scoped file IO refuses special or multiply linked files")
            fd = msvcrt.open_osfhandle(handle, (os.O_WRONLY if writing else os.O_RDONLY) | os.O_BINARY)
        except BaseException:
            _api().CloseHandle(handle)
            raise
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise PermissionError("Scoped file IO requires a regular file")
            if mode == "w":
                os.ftruncate(fd, 0)
            if mode == "a":
                os.lseek(fd, 0, os.SEEK_END)
            stream = os.fdopen(fd, mode, **({} if "b" in mode else {"encoding": encoding}))
        except BaseException:
            os.close(fd)
            raise
        with stream:
            yield stream


def mkdir_checked(path, create_roots=None, exist_ok=True):
    with checked_parent(path, create=True, create_roots=create_roots) as target:
        try:
            os.mkdir(target)
        except FileExistsError:
            if not exist_ok:
                raise
        handle, _ = _open(target, directory=True)
        _api().CloseHandle(handle)


@contextmanager
def _pin_target(path, *, directory=False):
    # Deny competing write opens, permit our own rename/delete through share-delete.
    handle, info = _open(path, access=0x10080, directory=directory, sharing=1)
    try:
        if not directory and info.links != 1:
            raise PermissionError("Scoped mutation refuses multiply linked files")
        yield handle
    finally:
        _api().CloseHandle(handle)


def _set_information(handle, info_class, info, size=None):
    api = _api()
    api.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    if not api.SetFileInformationByHandle(handle, info_class, ctypes.byref(info), size or ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())


def unlink_checked(path):
    with checked_parent(path) as target, _pin_target(target) as handle:
        _set_information(handle, 4, wintypes.BOOL(True))


def rename_checked(source, target):
    with checked_parent(source) as src, checked_parent(target) as dst, _pin_target(src) as handle:
        class RenameInfo(ctypes.Structure):
            _fields_ = [("replace", wintypes.BOOL), ("root", wintypes.HANDLE),
                        ("length", wintypes.DWORD), ("name", ctypes.c_wchar * 1)]
        # FileManager already requires a new destination. Do not overwrite any
        # destination inserted concurrently, including a reparse point.
        encoded = str(dst).encode("utf-16-le")
        buffer = ctypes.create_string_buffer(RenameInfo.name.offset + len(encoded) + 2)
        info = RenameInfo.from_buffer(buffer)
        info.replace = False
        info.root = None
        info.length = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.name.offset, encoded, len(encoded))
        _set_information(handle, 3, buffer, len(buffer))


def rmtree_checked(path):
    with checked_parent(path) as target:
        handle, _ = _open(target, directory=True)
        try:
            for entry in target.iterdir():
                attrs = os.lstat(entry).st_file_attributes
                if attrs & 0x400:
                    # Never traverse a junction or symbolic link.
                    if attrs & 0x10:
                        os.rmdir(entry)
                    else:
                        os.unlink(entry)
                elif attrs & 0x10:
                    rmtree_checked(entry)
                else:
                    unlink_checked(entry)
        finally:
            _api().CloseHandle(handle)
        os.rmdir(target)
