"""One bounded SEQPACKET request, optional stdio FDs, and control replies."""
from __future__ import annotations

import array
import fcntl
import json
import os
import socket
import stat

try:
    from .constants import MAX_PACKET
except ImportError:
    from constants import MAX_PACKET


def send_packet(connection: socket.socket, payload: dict, descriptors=()) -> None:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(data) > MAX_PACKET:
        raise ValueError("Sandbox message exceeds the protocol limit")
    ancillary = []
    if descriptors:
        ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", descriptors))]
    if connection.sendmsg([data], ancillary) != len(data):
        raise RuntimeError("Incomplete sandbox message")


def receive_packet(connection: socket.socket, max_fds=3) -> tuple[dict, list[int]]:
    data, ancillary, flags, _ = connection.recvmsg(MAX_PACKET + 1, socket.CMSG_SPACE(4 * max_fds), socket.MSG_CMSG_CLOEXEC)
    descriptors = []
    try:
        for level, kind, content in ancillary:
            if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
                raise ValueError("Unexpected sandbox ancillary data")
            values = array.array("i")
            values.frombytes(content[:len(content) - len(content) % values.itemsize])
            descriptors.extend(values)
        if not data:
            raise EOFError("Sandbox controller disconnected")
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(data) > MAX_PACKET or len(descriptors) > max_fds:
            raise ValueError("Truncated or oversized sandbox request")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Expected a sandbox message object")
        return payload, descriptors
    except BaseException:
        for fd in descriptors:
            os.close(fd)
        raise


def validate_stdio(descriptors: list[int]) -> None:
    if len(descriptors) != 3 or len(set(descriptors)) != 3:
        raise ValueError("Exactly three distinct stdio descriptors are required")
    for index, fd in enumerate(descriptors):
        mode = os.fstat(fd).st_mode
        if stat.S_ISDIR(mode) or not any(predicate(mode) for predicate in (
            stat.S_ISREG, stat.S_ISFIFO, stat.S_ISSOCK, stat.S_ISCHR,
        )):
            raise ValueError("Unsupported stdio descriptor")
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        access = flags & os.O_ACCMODE
        if flags & getattr(os, "O_PATH", 0) or (index == 0 and access == os.O_WRONLY) or (index > 0 and access == os.O_RDONLY):
            raise ValueError("Wrong stdio access mode")
