"""Kernel-attested callers; sandboxed peers cannot request a second sandbox."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import select
import socket
import struct

try:
    from .constants import PROFILE_PREFIX
except ImportError:
    from constants import PROFILE_PREFIX


@dataclass(frozen=True)
class Principal:
    pid: int
    uid: int
    gid: int
    groups: tuple[int, ...]
    pidfd: int

    def alive(self) -> bool:
        return not select.select([self.pidfd], [], [], 0)[0]


def identify_peer(connection: socket.socket, allowed_uids: set[int]) -> Principal:
    pid, uid, gid = struct.unpack("iII", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    # Python's getsockopt buffer limit is 1024 bytes; oversized labels fail
    # closed instead of being truncated or treated as an unconfined caller.
    label = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERSEC, 1024).rstrip(b"\0").decode()
    if PROFILE_PREFIX in label:
        raise PermissionError("Sandboxed processes cannot invoke the privileged helper")
    if uid not in allowed_uids:
        raise PermissionError("This OS user is not authorized for the sandbox helper")
    pidfd = os.pidfd_open(pid, 0)
    try:
        if select.select([pidfd], [], [], 0)[0]:
            raise PermissionError("Sandbox caller has already exited")
        fields = {}
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in {"Uid", "Gid", "Groups"}:
                fields[key] = tuple(int(item) for item in value.split())
        if fields.get("Uid") != (uid,) * 4 or fields.get("Gid") != (gid,) * 4:
            raise PermissionError("Mixed or changed caller credentials are not accepted")
        return Principal(pid, uid, gid, fields.get("Groups", ()), pidfd)
    except BaseException:
        os.close(pidfd)
        raise


def drop_to_caller(principal: Principal) -> None:
    """Only used in an isolated fork; never change a shared daemon's identity."""
    os.setgroups(list(principal.groups))
    os.setresgid(principal.gid, principal.gid, principal.gid)
    os.setresuid(principal.uid, principal.uid, principal.uid)
