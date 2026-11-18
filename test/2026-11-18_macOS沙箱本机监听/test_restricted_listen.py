"""macOS 受限网络档：本机监听与 Unix socket（2026-11-18）。

运行（独立进程，import 前注入隔离环境）::

    python3 -B test/2026-11-18_macOS沙箱本机监听/test_restricted_listen.py

两类用例：
- profile 文本断言（任何平台可跑）；
- 真机 sandbox-exec 行为（仅 macOS；嵌套在沙箱里 sandbox-exec 自身 EPERM 时跳过）。

已知且无法在 profile 层修复的边界（见 docs/agents_details/permission_and_sandbox.md）：
服务监听 0.0.0.0 / :: 时局域网可访问；本测试把它固定为「现状」用例，行为若变化需
回头更新文档与提示词。
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="astrion_sb_listen_"))
os.environ["ASTRION_IGNORE_DOTENV"] = "1"
os.environ["ASTRION_DATA_ROOT"] = str(_TMP_ROOT / "root")
os.environ["DATA_DIR"] = str(_TMP_ROOT / "root" / "data")
os.environ["LOGS_DIR"] = str(_TMP_ROOT / "root" / "logs")
os.environ["DEPLOY_CONFIG_DIR"] = str(_TMP_ROOT / "root" / "config")
os.environ["TERMINAL_SANDBOX_MODE"] = "host"
for _key in list(os.environ):
    if _key.startswith("HOST_SANDBOX_") and _key != "HOST_SANDBOX_ENABLED":
        os.environ.pop(_key, None)

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from modules.host_sandbox_runner import (  # noqa: E402
    _build_macos_network_policy,
    _macos_profile_for_workspace,
    _macos_readonly_profile_for_workspace,
)

_SANDBOX_EXEC = shutil.which("sandbox-exec")
_PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable

# 沙箱内的服务端：bind+listen 后打印端口，接受一个连接回 b"ok"
_TCP_SERVER = r"""
import socket, sys
s = socket.socket(socket.AF_INET6 if ":" in sys.argv[1] else socket.AF_INET)
try:
    s.bind((sys.argv[1], 0)); s.listen()
except OSError as e:
    print("ERR", e.errno, flush=True); sys.exit(0)
print("PORT", s.getsockname()[1], flush=True)
s.settimeout(10)
c, _ = s.accept(); c.sendall(b"ok"); c.close()
"""

_UNIX_ROUND_TRIP = r"""
import os, socket, sys
p = sys.argv[1]
try:
    s = socket.socket(socket.AF_UNIX); s.bind(p); s.listen()
    c = socket.socket(socket.AF_UNIX); c.connect(p)
    a, _ = s.accept(); c.sendall(b"hi"); print("OK", a.recv(2).decode())
except OSError as e:
    print("ERR", e.errno)
"""


def _sandbox_usable() -> bool:
    if platform.system() != "Darwin" or not _SANDBOX_EXEC:
        return False
    probe = subprocess.run([_SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
                           capture_output=True, timeout=10)
    return probe.returncode == 0


class ProfileTextTests(unittest.TestCase):
    def test_restricted_allows_loopback_listen(self):
        policy = _build_macos_network_policy("restricted")
        self.assertIn('(allow network-outbound (remote ip "localhost:*"))', policy)
        self.assertIn('(allow network-bind (local ip "localhost:*"))', policy)
        self.assertIn('(allow network-inbound (local ip "localhost:*"))', policy)
        self.assertNotIn("unix-socket", policy)
        self.assertNotIn("(allow network-outbound)\n", policy)

    def test_restricted_unix_socket_scoped_to_given_paths(self):
        policy = _build_macos_network_policy("restricted", unix_socket_paths=["/a/b"])
        self.assertIn('(allow network-bind (local unix-socket (subpath "/a/b")))', policy)
        self.assertIn('(allow network-outbound (remote unix-socket (subpath "/a/b")))', policy)

    def test_none_and_full_unchanged(self):
        self.assertEqual(_build_macos_network_policy("none", unix_socket_paths=["/a"]), "")
        self.assertEqual(_build_macos_network_policy("full", unix_socket_paths=["/a"]),
                         "(allow network-outbound)\n(allow network-inbound)\n")

    def test_profiles_wire_unix_sockets_only_in_writable(self):
        ws = _TMP_ROOT / "ws_text"
        ws.mkdir(exist_ok=True)
        writable = _macos_profile_for_workspace(ws, "restricted", ws)
        readonly = _macos_readonly_profile_for_workspace(ws, "restricted", ws)
        self.assertIn(f'(local unix-socket (subpath "{ws.resolve()}"))', writable)
        self.assertNotIn("unix-socket", readonly)
        self.assertIn("network-inbound", readonly)


@unittest.skipUnless(_sandbox_usable(), "需要 macOS 且能运行 sandbox-exec")
class SandboxBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="ws_", dir=_TMP_ROOT)).resolve()

    def _serve(self, profile: str, host: str):
        proc = subprocess.Popen([_SANDBOX_EXEC, "-p", profile, _PY, "-c", _TCP_SERVER, host],
                                cwd=self.ws, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (proc.kill(), proc.communicate()))
        line = proc.stdout.readline().split()
        return proc, line

    def _fetch(self, host: str, port: int) -> bytes:
        with socket.create_connection((host, port), timeout=5) as conn:
            return conn.recv(2)

    def test_restricted_loopback_server_reachable(self):
        for profile in (_macos_profile_for_workspace(self.ws, "restricted", self.ws),
                        _macos_readonly_profile_for_workspace(self.ws, "restricted", self.ws)):
            _, line = self._serve(profile, "127.0.0.1")
            self.assertEqual(line[0], "PORT", line)
            self.assertEqual(self._fetch("127.0.0.1", int(line[1])), b"ok")

    def test_none_still_blocks_listen(self):
        _, line = self._serve(_macos_profile_for_workspace(self.ws, "none", self.ws), "127.0.0.1")
        self.assertEqual(line, ["ERR", "1"])

    def test_restricted_outbound_still_loopback_only(self):
        profile = _macos_profile_for_workspace(self.ws, "restricted", self.ws)
        script = ("import socket\n"
                  "try:\n socket.create_connection(('1.1.1.1', 80), timeout=3); print('CONNECTED')\n"
                  "except OSError as e: print('ERR', e.errno)")
        out = subprocess.run([_SANDBOX_EXEC, "-p", profile, _PY, "-c", script],
                             cwd=self.ws, capture_output=True, text=True, timeout=20).stdout.split()
        self.assertEqual(out, ["ERR", "1"])

    def test_wildcard_bind_is_known_limit(self):
        # 现状：localhost 过滤器放行 0.0.0.0 绑定（局域网可达）；提示词负责要求 127.0.0.1
        _, line = self._serve(_macos_profile_for_workspace(self.ws, "restricted", self.ws), "0.0.0.0")
        self.assertEqual(line[0], "PORT", line)
        self.assertEqual(self._fetch("127.0.0.1", int(line[1])), b"ok")

    def test_unix_socket_in_workspace(self):
        sock = str(self.ws / "s.sock")
        writable = _macos_profile_for_workspace(self.ws, "restricted", self.ws)
        readonly = _macos_readonly_profile_for_workspace(self.ws, "restricted", self.ws)
        run = lambda p: subprocess.run([_SANDBOX_EXEC, "-p", p, _PY, "-c", _UNIX_ROUND_TRIP, sock],
                                       cwd=self.ws, capture_output=True, text=True, timeout=20).stdout.split()
        self.assertEqual(run(writable), ["OK", "hi"])
        os.unlink(sock)
        self.assertEqual(run(readonly)[0], "ERR")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
