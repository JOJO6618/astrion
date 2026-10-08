"""Unprivileged transport; disconnecting cancels exactly this sandbox task."""
from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import socket
import struct
import sys

try:
    from .constants import DEFAULT_SOCKET, PROTOCOL_VERSION
    from .schema import validate_request
    from .transport import receive_packet, send_packet
except ImportError:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from constants import DEFAULT_SOCKET, PROTOCOL_VERSION
    from schema import validate_request
    from transport import receive_packet, send_packet


def connect(socket_path: str = DEFAULT_SOCKET) -> socket.socket:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    connection.settimeout(6)
    try:
        connection.connect(socket_path)
        _, uid, _ = struct.unpack("iII", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 0:
            raise PermissionError("The sandbox helper peer is not the administrator service")
        return connection
    except BaseException:
        connection.close()
        raise


def probe(socket_path: str = DEFAULT_SOCKET) -> dict:
    with connect(socket_path) as connection:
        send_packet(connection, {"operation": "status"})
        payload, descriptors = receive_packet(connection, 0)
        if descriptors or payload.get("error") or payload.get("protocol") != PROTOCOL_VERSION or not payload.get("ready"):
            raise RuntimeError(payload.get("error", "Unsupported sandbox helper"))
        return payload


def run(request: dict, socket_path: str = DEFAULT_SOCKET) -> int:
    validate_request(request)
    connection = connect(socket_path)
    def stop(_signal, _frame):
        connection.close()
        raise SystemExit(130)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        send_packet(connection, request, [0, 1, 2])
        connection.settimeout(25)
        while True:
            payload, descriptors = receive_packet(connection, 0)
            if descriptors:
                raise ValueError("Unexpected helper descriptors")
            if payload.get("error"):
                print(f"Linux sandbox: {payload['error']}", file=sys.stderr)
            if "exitcode" in payload:
                code = payload["exitcode"]
                return code if code >= 0 else 128 - code
            if payload.get("started"):
                connection.settimeout(None)
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    # Only useful to isolated experiment harnesses; root still authenticates
    # config and UID. The application's plan always uses the fixed socket.
    parser.add_argument("--socket", default=DEFAULT_SOCKET)
    args = parser.parse_args()
    return run(json.loads(base64.b64decode(args.request, validate=True)), args.socket)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Linux sandbox unavailable: {error}", file=sys.stderr)
        sys.exit(125)
