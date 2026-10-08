"""Pin mount sources in a fork with the actual caller's DAC credentials."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import socket
import stat
import signal
import time

try:
    from .constants import MAX_PATHS, SYSTEM_DIRECTORIES, SYSTEM_FILES
    from .principal import drop_to_caller
    from .transport import receive_packet, send_packet
except ImportError:
    from constants import MAX_PATHS, SYSTEM_DIRECTORIES, SYSTEM_FILES
    from principal import drop_to_caller
    from transport import receive_packet, send_packet


def open_nofollow(path: str) -> int:
    descriptor = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in PurePosixPath(path).parts[1:]:
            next_fd = os.open(component, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            information = os.fstat(next_fd)
            if stat.S_ISLNK(information.st_mode):
                os.close(next_fd)
                raise PermissionError("A sandbox source changed into a symlink")
            os.close(descriptor)
            descriptor = next_fd
        mode = os.fstat(descriptor).st_mode
        if not any(predicate(mode) for predicate in (stat.S_ISDIR, stat.S_ISREG, stat.S_ISSOCK)):
            raise PermissionError("Device, FIFO, and special sandbox grants are refused")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def specifications(request: dict) -> list[dict]:
    sources = []
    # Canonical resolution of fixed administrator-owned system aliases only.
    for raw in (*SYSTEM_DIRECTORIES, *SYSTEM_FILES):
        path = Path(raw)
        if path.exists() and not path.is_symlink():
            sources.append({"source": str(path), "target": raw, "write": False, "system": True})
        elif path.exists() and raw in SYSTEM_FILES:
            sources.append({"source": str(path.resolve()), "target": raw, "write": False, "system": True})
    extra_writes = set(request["writes"])
    for path in request["reads"]:
        sources.append({"source": path, "target": path, "write": path in extra_writes, "system": False})
    for path in request["writes"]:
        if path not in request["reads"]:
            sources.append({"source": path, "target": path, "write": True, "system": False})
    sources.append({"source": request["workspace"], "target": request["workspace"],
                    "write": not request["readonly"], "system": False})
    unique = {}
    for item in sources:
        if item["target"] not in unique:
            unique[item["target"]] = item
        elif item["target"] == request["workspace"]:
            unique[item["target"]] = item
    return list(unique.values())


def _open_sources(connection: socket.socket, principal, request: dict, items: list[dict]) -> None:
    descriptors = []
    try:
        drop_to_caller(principal)
        for item in items:
            fd = open_nofollow(item["source"])
            information = os.fstat(fd)
            if item["write"] and stat.S_ISREG(information.st_mode) and information.st_nlink != 1:
                os.close(fd)
                raise PermissionError("Multi-link writable file grants are refused")
            descriptors.append(fd)
        workspace_fd = descriptors[next(index for index, item in enumerate(items)
                                        if item["target"] == request["workspace"])]
        if not stat.S_ISDIR(os.fstat(workspace_fd).st_mode):
            raise PermissionError("Workspace is not a directory")
        relative = PurePosixPath(request["cwd"]).relative_to(request["workspace"])
        current = os.dup(workspace_fd)
        try:
            for part in relative.parts:
                next_fd = os.open(part, os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=current)
                os.close(current)
                current = next_fd
            descriptors.append(current)
            current = -1
        finally:
            if current >= 0:
                os.close(current)
        send_packet(connection, {"ok": True}, descriptors)
    except Exception as exc:
        send_packet(connection, {"ok": False, "error": str(exc)})
    finally:
        for fd in descriptors:
            os.close(fd)
        connection.close()


def pin_sources(principal, request: dict) -> list[dict]:
    items = specifications(request)
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    pid = os.fork()
    if pid == 0:
        parent.close()
        try:
            _open_sources(child, principal, request, items)
        finally:
            os._exit(0)
    child.close()
    parent.settimeout(5)
    descriptors = []
    try:
        result, descriptors = receive_packet(parent, MAX_PATHS + len(SYSTEM_DIRECTORIES) + len(SYSTEM_FILES) + 1)
        if not result.get("ok"):
            raise PermissionError(result.get("error", "Sandbox source verification failed"))
        if len(descriptors) != len(items) + 1:
            raise PermissionError("Incomplete verified sandbox sources")
        return [{**item, "fd": fd} for item, fd in zip(items, descriptors[:-1])] + [
            {"source": request["cwd"], "target": "cwd", "fd": descriptors[-1]}]

    except BaseException:
        for fd in descriptors:
            os.close(fd)
        raise
    finally:
        parent.close()
        reap_child(pid)


def reap_child(pid: int) -> None:
    """Only our unreaped fork PID is signalled; never block on filesystem IO."""
    try:
        if os.waitpid(pid, os.WNOHANG)[0]:
            return
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
            time.sleep(0.01)
    except (ChildProcessError, ProcessLookupError):
        pass
