"""HTTP contracts against the compiled Host binary and an isolated DATA_DIR."""

import http.cookiejar
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
TABS = "/api/conversation-tabs"
CHILDREN = "/api/sub_agents"


class AssembledContract(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="assembled-nav-", dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.workspaces = {}
        for workspace_id in ("ws_a", "ws_b"):
            path = self.root / workspace_id
            path.mkdir()
            self.workspaces[workspace_id] = {"workspace_id": workspace_id, "path": str(path),
                                             "label": f"Label {workspace_id}"}
        catalog = self.root / "host_workspaces.json"
        catalog.write_text(json.dumps({"workspaces": list(self.workspaces.values()),
                                       "default_workspace_id": "ws_a"}), encoding="utf-8")
        binary = Path(os.environ.get("ASTRION_HEADLESS_BIN", ROOT / "backend-rust/target/debug/astrion-headless"))
        if not binary.is_file():
            self.fail(f"Build Rust Host binary before running this contract: {binary}")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.origin = f"http://127.0.0.1:{port}"
        self.public = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.browser = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                   urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = None
        server_log = self.root / "server.log"
        with server_log.open("wb") as log:
            self.process = subprocess.Popen(
                [str(binary.resolve()), "--web", "--workspace", self.workspaces["ws_a"]["path"],
                 "--data-dir", str(self.data), "--static-root", str(self.root), "--port", str(port)],
                cwd=self.root, stdin=subprocess.PIPE, stderr=log, stdout=log,
                env={"PATH": os.environ["PATH"], "ASTRION_DESKTOP_STOP_STDIN": "1",
                     "HOST_WORKSPACES_FILE": str(catalog), "ASTRION_RESOURCE_ROOT": str(self.root)},
            )
        self.addCleanup(self.stop)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and self.process.poll() is None:
            try:
                self.read(TABS, expected=401, browser=False)
                break
            except urllib.error.URLError:
                time.sleep(0.02)
        else:
            self.fail(server_log.read_text(encoding="utf-8"))
        self.token = (self.data / "host_api_token").read_text(encoding="utf-8").strip()
        self.csrf = self.read("/api/csrf-token")["token"]
        self.read("/host-login", {"password": ""})
        self.csrf = self.read("/api/csrf-token")["token"]

    def stop(self):
        if self.process.poll() is not None:
            return
        self.process.stdin.close()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.fail("Rust Host fixture did not stop after stdin EOF")

    def read(self, path, payload=None, *, expected=200, browser=True, bearer=False, workspace=None):
        headers = {"Origin": self.origin}
        if bearer:
            headers["Authorization"] = "Bearer " + self.token
        if workspace:
            headers["X-Astrion-Workspace-Id"] = workspace
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            if browser and self.csrf:
                headers["X-CSRF-Token"] = self.csrf
            data = json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(self.origin + path, data=data, headers=headers)
        try:
            response = (self.browser if browser else self.public).open(request, timeout=4)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            if response.status != expected:
                self.fail(f"{path}: expected {expected}, got {response.status}: {body!r}")
            return json.loads(body)

    def command(self, action, payload, *, expected=200, command_id=None):
        return self.read(TABS + "/commands", {"command_id": command_id or uuid.uuid4().hex,
                                             "action": action, "payload": payload}, expected=expected)

    def session(self, workspace):
        return self.read("/api/runtime/sessions", {"workspace_id": workspace}, browser=False,
                         bearer=True, workspace=workspace)["conversation_id"]

    def test_multi_workspace_navigation_receipts_and_child_agent_views(self):
        snapshot = self.read(TABS)
        self.assertEqual((snapshot["version"], snapshot["revision"], snapshot["tabs"]), (3, 1, []))
        parent_a = self.session("ws_a")
        parent_b = self.session("ws_b")
        sibling_a = self.session("ws_a")
        self.assertNotEqual(parent_a, parent_b)
        self.assertEqual(self.command("open", {"workspace_id": "ws_b", "conversation_id": parent_a},
                                      expected=409)["code"], "navigation_superseded")
        first = self.command("open", {"workspace_id": "ws_a", "conversation_id": parent_a, "key": "a"})
        second = self.command("open", {"workspace_id": "ws_b", "conversation_id": parent_b, "key": "b"})
        self.assertEqual(first["state"]["tabs"][0]["workspace_label"], "Label ws_a")
        self.assertEqual(second["state"]["tabs"][1]["workspace_label"], "Label ws_b")
        detached = self.command("detach", {"key": "b"}, command_id="detached")
        self.assertEqual(detached["state"]["active_key"], "a")
        self.assertEqual(self.command("detach", {"key": "b"}, command_id="detached"), detached)
        self.assertEqual(self.command("open", {"workspace_id": "ws_b", "conversation_id": parent_b})["result"],
                         {"focus": "detached", "key": "b"})
        self.assertEqual(self.read(TABS + "/activity")["data"], [])

        records = {}
        for task_id, parent, workspace in (("sub_a", parent_a, "ws_a"), ("sub_b", parent_b, "ws_b"),
                                           ("sub_other", sibling_a, "ws_a")):
            root = self.data / "sub_agent_tasks" / task_id
            root.mkdir(parents=True, exist_ok=True)
            (root / "progress.jsonl").write_text(f'{{"identity":"{task_id}"}}\n', encoding="utf-8")
            records[task_id] = {"task_id": task_id, "conversation_id": parent,
                                "workspace_root": self.workspaces[workspace]["path"],
                                "name": task_id, "created_at": 1, "status": "running",
                                "progress_file": str(self.root / "untrusted")}
        (self.data / "sub_agent_runtime.json").write_text(json.dumps({"version": 1, "tasks": records}),
                                                         encoding="utf-8")
        for browser in (True, False):
            for task_id, parent, workspace in (("sub_a", parent_a, "ws_a"), ("sub_b", parent_b, "ws_b")):
                listing = self.read(f"{CHILDREN}?conversation_id={parent}", browser=browser,
                                    bearer=not browser, workspace=workspace)["data"]
                self.assertEqual([(row["task_id"], row["status"]) for row in listing], [(task_id, "stopped")])
        route = CHILDREN + "/sub_a/activity"
        page = self.read(f"{route}?conversation_id={parent_a}", workspace="ws_a")["data"]
        self.assertEqual(page["entries"], [{"identity": "sub_a"}])
        self.assertEqual(page["status"], "stopped")
        self.read(f"{route}?conversation_id={parent_a}", expected=401, browser=False, bearer=True,
                  workspace="ws_a")
        self.read(f"{route}?conversation_id={sibling_a}", expected=404, workspace="ws_a")
        self.read(f"{route}?conversation_id={parent_b}", expected=404, workspace="ws_b")
        self.read(f"{route}?conversation_id={parent_a}&offset=0&before=0", expected=400, workspace="ws_a")


if __name__ == "__main__":
    unittest.main()
