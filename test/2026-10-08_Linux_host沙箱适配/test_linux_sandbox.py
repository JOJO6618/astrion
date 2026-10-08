from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import unittest
from unittest.mock import patch, Mock

from modules.linux_sandbox.schema import RequestError, validate_request
from modules.linux_sandbox.transport import receive_packet, send_packet
from modules.linux_sandbox.supervisor import unit_command
from modules.linux_sandbox.profiles import profile_text
from modules.linux_sandbox.mounts import build_command


def request(**changes):
    return {"operation": "run", "workspace": "/workspace", "cwd": "/workspace", "reads": [],
            "writes": [], "readonly": False, "workspace_only": True, "network": "restricted",
            "argv": ["/bin/bash", "-c", "echo ok"], "env": {}, "umask": 0o022, **changes}


class SchemaTests(unittest.TestCase):
    def test_valid_policies(self):
        for network in ("full", "restricted", "none"):
            self.assertEqual(validate_request(request(network=network))["network"], network)

    def test_rejects_privileged_and_unknown_fields(self):
        for field, value in (("uid", 0), ("bwrap", "/tmp/malicious"), ("property", "User=root"), ("unexpected", True)):
            with self.subTest(field=field), self.assertRaises(RequestError):
                validate_request(request(**{field: value}))

    def test_rejects_escape_and_runtime_paths(self):
        for path in ("/", "/workspace/../outside", "relative", "/workspace//sub", "/.astrion-runtime/home",
                     "/proc", "/proc/self/root", "/dev", "/sys/fs/cgroup"):
            with self.subTest(path=path), self.assertRaises(RequestError):
                validate_request(request(reads=[path]))
        with self.assertRaises(RequestError):
            validate_request(request(cwd="/workspace-other"))

    def test_scope_write_boundaries(self):
        with self.assertRaises(RequestError):
            validate_request(request(writes=["/outside"]))
        with self.assertRaises(RequestError):
            validate_request(request(readonly=True, workspace_only=False, writes=["/outside"]))
        self.assertEqual(validate_request(request(workspace_only=False, writes=["/outside"]))["writes"], ["/outside"])

    def test_root_loader_environment_is_not_requestable(self):
        for key in ("LD_PRELOAD", "LD_LIBRARY_PATH", "GCONV_PATH", "BASH_ENV", "ENV"):
            with self.subTest(key=key), self.assertRaises(RequestError):
                validate_request(request(env={key: "/tmp/injection"}))

    def test_input_bounds(self):
        for changes in ({"argv": []}, {"env": {"PATH": "x" * 8193}}, {"umask": True}, {"network": "fallback"},
                        {"reads": ["/root/" + str(i) for i in range(129)]}):
            with self.subTest(changes=changes), self.assertRaises(RequestError):
                validate_request(request(**changes))


@unittest.skipUnless(hasattr(socket, "MSG_CMSG_CLOEXEC"), "Linux ancillary fd handling")
class TransportTests(unittest.TestCase):
    def test_packet_roundtrip(self):
        first, second = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            send_packet(first, {"operation": "status"})
            payload, fds = receive_packet(second, 0)
            self.assertEqual(payload, {"operation": "status"})
            self.assertEqual(fds, [])
        finally:
            first.close(); second.close()

    @unittest.skipUnless(hasattr(socket, "MSG_CMSG_CLOEXEC"), "Linux ancillary fd handling")
    def test_oversized_packet_fails_closed(self):
        first, second = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            first.send(b'x' * 65537)
            with self.assertRaises(ValueError):
                receive_packet(second)
        finally:
            first.close(); second.close()


class ConstructionTests(unittest.TestCase):
    def test_network_properties_and_lifetime_ownership(self):
        config = {"systemd_run": "/usr/bin/systemd-run", "python": "/usr/bin/python3",
                  "install_root": "/usr/local/libexec/astrion-sandbox", "runtime_max_sec": 3600,
                  "socket": "/run/astrion-sandbox/control.sock"}
        for network in ("full", "restricted", "none"):
            command = unit_command(config, "astrion-sandbox-job-fixture", network, "/run/job/control", "/etc/helper.json")
            self.assertIn("--property=KillMode=control-group", command)
            self.assertIn("--property=BindsTo=astrion-sandbox.service", command)
            self.assertEqual("--property=IPAddressDeny=any" in command, network != "full")
            self.assertEqual("--property=IPAddressAllow=localhost" in command, network == "restricted")

    def test_profiles_enforce_abstract_socket_policy_without_exec_escape(self):
        profile = profile_text()
        self.assertIn('deny unix (connect, send, receive) peer=(addr="@**")', profile)
        self.assertNotIn("change_profile", profile)
        self.assertNotIn('addr="/**"', profile)

    def test_pure_virtual_parent_permissions_never_change_bound_sources(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            fd = os.open(root, os.O_RDONLY)
            self.addCleanup(os.close, fd)
            sources = [{"target": "/parent/authorized", "fd": fd, "write": True},
                       {"target": "/parent/authorized/nested", "fd": fd, "write": False},
                       {"target": "cwd", "fd": fd}]
            config = {"bwrap": "/usr/bin/bwrap", "install_root": "/usr/local/libexec/astrion-sandbox"}
            with patch("modules.linux_sandbox.mounts.SYSTEM_DIRECTORIES", ()):
                command = build_command(config, request(), {"uid": 1000, "gid": 1000, "groups": []}, sources, "/run/lease", fd)
            modified = [command[i+2] for i, token in enumerate(command) if token == "--chmod"]
            self.assertEqual(modified, ["/parent"])
            self.assertNotIn("--unshare-net", command)
            self.assertNotIn(["--ro-bind", "/", "/"], [command[i:i+3] for i in range(len(command))])

    def test_application_plans_encode_trusted_scope_and_fail_closed(self):
        from modules.linux_sandbox.plans import build_plan
        from modules.host_sandbox_runner import HostSandboxError
        from modules.execution_scope import ExecutionScope, bind_execution_scope
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp).resolve()
            nested = root / "nested"
            nested.mkdir()
            scope = ExecutionScope("sub_agent", "fixture", root, "workspace_write")
            with bind_execution_scope(scope), patch("modules.linux_sandbox.plans.probe"), \
                 patch("modules.linux_sandbox.plans.get_macos_readable_paths", return_value=["/authorized"]), \
                 patch("modules.linux_sandbox.plans.get_macos_writable_paths", return_value=["/outside"]):
                plan = build_plan(nested, {"LD_PRELOAD": "evil", "PATH": "/usr/bin"}, ["true"], "none")
                payload = json.loads(base64.b64decode(plan.command[-1]))
                self.assertEqual(payload["workspace"], str(root))
                self.assertEqual(payload["writes"], [])
                self.assertTrue(payload["workspace_only"])
                self.assertNotIn("LD_PRELOAD", payload["env"])
            with patch("modules.linux_sandbox.plans.probe", side_effect=OSError("offline")), \
                 patch("modules.linux_sandbox.plans.get_macos_readable_paths", return_value=[]), \
                 patch("modules.linux_sandbox.plans.get_macos_writable_paths", return_value=[]):
                with self.assertRaises(HostSandboxError):
                    build_plan(root, {}, ["true"])


@unittest.skipUnless(hasattr(os, "O_PATH"), "Linux source descriptor checks")
class SourceTests(unittest.TestCase):
    def test_no_symlink_or_special_mount_sources(self):
        from modules.linux_sandbox.sources import open_nofollow
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp).resolve()
            (root / "regular").write_text("fixture")
            (root / "link").symlink_to(root / "regular")
            os.mkfifo(root / "fifo")
            for source in (root / "link", root / "fifo"):
                with self.assertRaises(PermissionError):
                    open_nofollow(str(source))
            fd = open_nofollow(str(root / "regular"))
            try:
                self.assertTrue(stat.S_ISREG(os.fstat(fd).st_mode))
            finally:
                os.close(fd)


if __name__ == "__main__":
    unittest.main()
