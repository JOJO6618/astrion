"""前端产物哈希化 + 服务端注入的回归测试。

运行（需先 `npm run build`，测试读取真实构建产物）：

    /opt/homebrew/bin/python3.11 -B test/2026-10-15_前端产物hash化/test_entry_assets.py

覆盖：
1. `_strip_content_hash` 对 8 位内容哈希的识别（含 base64url 的 `-`/`_`）
2. 各 HTML 入口的注入结果：dist 引用被替换为 manifest 中的真实文件名
3. 非 dist 引用（security.js、favicon 等）保持原样
4. 引用了 manifest 中不存在的产物时显式报错（fail-fast，不回退成 404）
5. `serve_entry_html` 的响应头为 no-store，避免引用被缓存
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from flask import Flask

from server.entry_assets import (
    EntryAssetError,
    _strip_content_hash,
    inject_entry_assets,
    serve_entry_html,
)

PAGES_WITH_DIST = [
    "index.html",
    "login.html",
    "register.html",
    "quick.html",
    "chrome.html",
    "admin_dashboard/index.html",
    "admin_policy/index.html",
    "custom_tools/index.html",
    "admin_api/index.html",
]

PAGES_WITHOUT_DIST = ["terminal.html", "resource_busy.html"]


def _app() -> Flask:
    return Flask(__name__, static_folder=str(ROOT / "static"))


class StripContentHashTest(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(_strip_content_hash("assets/auth-1a2b3c4d.css"), "assets/auth.css")

    def test_base64url_chars(self) -> None:
        self.assertEqual(
            _strip_content_hash("assets/quick-Dmc_QKwx.js"), "assets/quick.js"
        )
        self.assertEqual(
            _strip_content_hash("assets/main-CNtcLhgS.js"), "assets/main.js"
        )

    def test_name_with_dots(self) -> None:
        self.assertEqual(
            _strip_content_hash("assets/katex.min-DugUm48Z.js"), "assets/katex.min.js"
        )

    def test_without_hash(self) -> None:
        self.assertIsNone(_strip_content_hash("assets/main.js"))


class InjectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        manifest = ROOT / "static" / "dist" / ".vite" / "manifest.json"
        if not manifest.is_file():
            raise unittest.SkipTest("缺少 static/dist 构建产物，请先执行 npm run build")
        cls.app = _app()

    def _inject(self, name: str) -> str:
        raw = (ROOT / "static" / name).read_text(encoding="utf-8")
        with self.app.app_context():
            return inject_entry_assets(raw)

    def test_dist_refs_are_hashed(self) -> None:
        for name in PAGES_WITH_DIST:
            with self.subTest(page=name):
                out = self._inject(name)
                # 注入后不应再有「无哈希」的 dist 产物引用
                self.assertNotRegex(
                    out,
                    r"/static/dist/assets/[A-Za-z0-9_.]+\.(?:js|css|mjs)",
                    f"{name} 仍存在未哈希的产物引用",
                )
                self.assertRegex(out, r"/static/dist/assets/[\w.-]+-[A-Za-z0-9_-]{8}\.")

    def test_non_dist_refs_untouched(self) -> None:
        for name in PAGES_WITHOUT_DIST:
            with self.subTest(page=name):
                raw = (ROOT / "static" / name).read_text(encoding="utf-8")
                self.assertEqual(self._inject(name), raw)

        out = self._inject("index.html")
        self.assertIn("/static/security.js", out)
        self.assertIn("/static/easter-eggs/snake.js", out)

    def test_index_style_assembly_mapped(self) -> None:
        out = self._inject("index.html")
        self.assertNotIn('href="/static/style.css"', out)
        self.assertRegex(out, r"/static/dist/style-[0-9a-f]{10}\.css")

    def test_unknown_dist_ref_fails_fast(self) -> None:
        with self.app.app_context():
            with self.assertRaises(EntryAssetError):
                inject_entry_assets('<script src="/static/dist/assets/does-not-exist.js"></script>')

    def test_existing_dist_file_without_mapping_kept(self) -> None:
        """dist 里真实存在、但不在映射表里的文件（如尚未切换的旧构建产物）原样保留。

        这保证「新代码 + 旧 dist」兼容：部署时先更新代码再更新 dist 不会 502/404。
        """
        candidates = sorted((ROOT / "static" / "dist" / "assets").glob("*.css"))
        self.assertTrue(candidates, "dist 里应有 CSS 产物")
        ref = f"/static/dist/assets/{candidates[0].name}"
        with self.app.app_context():
            out = inject_entry_assets(f'<link rel="stylesheet" href="{ref}">')
        self.assertIn(ref, out)

    def test_missing_manifest_fails_fast(self) -> None:
        empty = Flask(__name__, static_folder=str(ROOT / "test"))
        with empty.app_context():
            with self.assertRaises(EntryAssetError):
                inject_entry_assets('<script src="/static/dist/assets/main.js"></script>')


class ServeEntryHtmlTest(unittest.TestCase):
    def test_no_store_header(self) -> None:
        app = _app()
        with app.test_request_context("/"):
            response = serve_entry_html("login.html")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("text/html", response.headers["Content-Type"])
        self.assertIn("/static/dist/assets/login-", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
