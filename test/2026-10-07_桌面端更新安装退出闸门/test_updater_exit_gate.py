"""桌面端更新安装退出闸门回归（离线，不启动服务/不启动 TUI）。

背景：electron-updater 的 quitAndInstall() 是「先关闭所有窗口、之后才给 app 发
before-quit」。桌面壳原先只在 close 里判断 appQuitting（只在 before-quit 置位），
配合「快捷对话已开启」把关闭改成 hide()，导致应用永远退不出去——表现为窗口消失、
程序坞图标还在、点一下又弹回来，而 ShipIt 等不到「无运行实例」，最后以
SQRLInstallerErrorDomain -9 "App Still Running Error" 放弃安装。

本用例守住两条不变量：
  1. updater-state.js 的开关语义正确（默认关闭，可开可关）；
  2. 所有会拦住退出的 close 闸门都带上了安装期放行条件，且 bridge 在调用
     quitAndInstall() 之前先开闸、并挂了兜底退出。

运行：python3 -m unittest discover -s test/2026-10-07_桌面端更新安装退出闸门 -p 'test_*.py'
"""

import json
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop-electron" / "src"


def read(rel):
    return (DESKTOP / rel).read_text(encoding="utf-8")


class UpdaterStateModuleTest(unittest.TestCase):
    """开关模块本身的语义（用 node 直接跑，模块无 electron 依赖）。"""

    def test_state_transitions(self):
        script = (
            "import { isUpdaterInstalling, setUpdaterInstalling } from './src/updater-state.js';"
            "const out = [isUpdaterInstalling()];"
            "setUpdaterInstalling(true); out.push(isUpdaterInstalling());"
            "setUpdaterInstalling(false); out.push(isUpdaterInstalling());"
            "setUpdaterInstalling(1); out.push(isUpdaterInstalling());"
            "setUpdaterInstalling(0); out.push(isUpdaterInstalling());"
            "console.log(JSON.stringify(out));"
        )
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=str(ROOT / "desktop-electron"),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout.strip()), [False, True, False, True, False])


class CloseGateTest(unittest.TestCase):
    """主窗口 / 快捷窗的 close 闸门必须带安装期放行条件。"""

    def test_main_window_close_gate(self):
        src = read("window.js")
        gate = re.search(r"mainWindow\.on\('close'.*?\n\s*\}\);", src, re.S)
        self.assertIsNotNone(gate, "未找到主窗口 close 处理器")
        body = gate.group(0)
        self.assertIn("isUpdaterInstalling()", body)
        self.assertIn("appQuitting", body)
        self.assertIn("quickEnabled()", body)
        self.assertIn("preventDefault()", body)

    def test_quick_window_close_gate(self):
        src = read("quick/controller.js")
        gate = re.search(r"quickWindow\.on\('close'.*?\n", src)
        self.assertIsNotNone(gate, "未找到快捷窗 close 处理器")
        body = gate.group(0)
        self.assertIn("isUpdaterInstalling()", body)
        self.assertIn("quitting", body)
        self.assertIn("preventDefault()", body)

    def test_no_unguarded_close_gate_left(self):
        """任何 close 处理器里出现 preventDefault 时都必须同时带安装期放行。"""
        for rel in ("window.js", "quick/controller.js"):
            src = read(rel)
            for match in re.finditer(r"\.on\('close'[^\n]*", src):
                line = match.group(0)
                if "preventDefault()" in line or "preventDefault" in line:
                    self.assertIn("isUpdaterInstalling()", line, f"{rel}: {line}")

    def test_focus_main_window_blocked_while_installing(self):
        src = read("window.js")
        fn = re.search(r"export function focusMainWindow\(route = ''\) \{(.*?)const target", src, re.S)
        self.assertIsNotNone(fn)
        self.assertIn("isUpdaterInstalling()", fn.group(1))

    def test_activate_and_second_instance_blocked_while_installing(self):
        src = read("main.js")
        for event in ("second-instance", "activate"):
            block = re.search(r"app\.on\('%s'.*?\n  \}\);" % event, src, re.S)
            self.assertIsNotNone(block, f"未找到 {event} 处理器")
            self.assertIn("isUpdaterInstalling()", block.group(0), event)

    def test_quick_entry_show_blocked_while_installing(self):
        src = read("quick/controller.js")
        fn = re.search(r"export async function showQuickEntry\(\) \{(.*?)try \{ await quickRendererReady", src, re.S)
        self.assertIsNotNone(fn)
        self.assertIn("isUpdaterInstalling()", fn.group(1))


class BridgeInstallFlowTest(unittest.TestCase):
    """控制桥：先开闸 → quitAndInstall → 兜底退出阶梯。"""

    def setUp(self):
        self.src = read("bridge.js")

    def test_gate_opened_before_quit_and_install(self):
        handler = re.search(r"autoUpdater\.on\('update-downloaded'.*?\n  \}\);", self.src, re.S)
        self.assertIsNotNone(handler, "未找到 update-downloaded 处理器")
        body = handler.group(0)
        self.assertIn("setUpdaterInstalling(true)", body)
        self.assertIn("autoUpdater.quitAndInstall()", body)
        self.assertLess(
            body.index("setUpdaterInstalling(true)"),
            body.index("autoUpdater.quitAndInstall()"),
            "必须先开闸再调用 quitAndInstall()",
        )
        self.assertIn("armUpdaterExitFallback()", body)

    def test_error_event_closes_gate_again(self):
        handler = re.search(r"autoUpdater\.on\('error'.*?\n  \}\);", self.src, re.S)
        self.assertIsNotNone(handler, "未找到 error 处理器")
        self.assertIn("setUpdaterInstalling(false)", handler.group(0))

    def test_fallback_exit_ladder_exists(self):
        self.assertIn("function armUpdaterExitFallback()", self.src)
        self.assertIn("app.quit()", self.src)
        self.assertIn("app.exit(0)", self.src)
        # app.exit() 不触发 will-quit，必须显式收后端，否则会留下孤儿 Python 进程
        self.assertIn("shutdownBackend()", self.src)

    def test_no_circular_import_of_lifecycle(self):
        """桥只依赖 backend.js（无反向依赖），不要 import lifecycle.js 造成环。"""
        self.assertNotIn("from './lifecycle.js'", self.src)


if __name__ == "__main__":
    unittest.main()
