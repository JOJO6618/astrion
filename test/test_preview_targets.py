"""modules/preview_targets.py 的 URL 识别规则测试（2026-09-29 简化重写后新增）。

覆盖 2026-09-27/28 桌面端对话实测的四类脏数据：
1. Markdown 加粗 + 中文粘连（`**url**（中文）`）
2. 浏览器控制台日志行号后缀（`url/favicon.ico:0`）
3. 代码模板占位符假地址（`f"http://127.0.0.1:{port}/"`）
4. 裸主机散文提及（`http://127.0.0.1` 无端口无路径）
"""

import unittest

from modules.preview_targets import (
    _normalize,
    _origin,
    _server_label,
    scan_stream_urls,
    scan_text_for_local_urls,
)


class ScanTextTest(unittest.TestCase):
    def test_plain_url_with_port_and_path(self):
        self.assertEqual(
            scan_text_for_local_urls("访问 http://127.0.0.1:8321/index.html 即可"),
            ["http://127.0.0.1:8321/index.html"],
        )

    def test_markdown_bold_and_cjk_glue_truncated(self):
        # 实测脏数据：index.html**（预览面板里也有） 整段被粘进 URL
        self.assertEqual(
            scan_text_for_local_urls("地址：**http://127.0.0.1:8321/index.html**（预览面板里也有）。"),
            ["http://127.0.0.1:8321/index.html"],
        )

    def test_fullwidth_parens_cut(self):
        self.assertEqual(
            scan_text_for_local_urls("http://localhost:5173/（这个地址）"),
            ["http://localhost:5173"],
        )

    def test_console_line_suffix_not_glued(self):
        # Chrome 控制台格式：@ url:行号:列号；`:` 不在路径白名单内
        self.assertEqual(
            scan_text_for_local_urls("Failed to load resource @ http://127.0.0.1:8321/app.js:1:24"),
            ["http://127.0.0.1:8321/app.js"],
        )

    def test_favicon_noise_dropped(self):
        self.assertEqual(
            scan_text_for_local_urls("404 @ http://127.0.0.1:8321/favicon.ico:0"),
            [],
        )

    def test_bare_host_dropped(self):
        self.assertEqual(scan_text_for_local_urls("见 http://127.0.0.1 即可"), [])
        self.assertEqual(scan_text_for_local_urls("见 http://localhost 即可"), [])

    def test_code_template_placeholder_dropped(self):
        # Rust/Python 模板字符串里的假地址：端口是占位符，落成裸主机后拒绝
        self.assertEqual(
            scan_text_for_local_urls('url = f"http://127.0.0.1:{ctx[\'bridge_port\']}{path}"'),
            [],
        )
        self.assertEqual(
            scan_text_for_local_urls('format!("http://127.0.0.1:{port}/")'),
            [],
        )

    def test_port_only_root_kept_and_slash_normalized(self):
        self.assertEqual(
            scan_text_for_local_urls("Local: http://localhost:5173/"),
            ["http://localhost:5173"],
        )

    def test_zero_host_normalized(self):
        self.assertEqual(
            scan_text_for_local_urls("Serving on http://0.0.0.0:8000/"),
            ["http://127.0.0.1:8000"],
        )

    def test_ipv6_loopback(self):
        self.assertEqual(
            scan_text_for_local_urls("见 http://[::1]:8321/a.html"),
            ["http://[::1]:8321/a.html"],
        )

    def test_dedup_and_order(self):
        text = "http://localhost:3000/a 与 http://localhost:3000/b 再提 http://localhost:3000/a"
        self.assertEqual(
            scan_text_for_local_urls(text),
            ["http://localhost:3000/a", "http://localhost:3000/b"],
        )

    def test_trailing_punctuation_stripped(self):
        self.assertEqual(
            scan_text_for_local_urls("打开 http://localhost:8080/docs."),
            ["http://localhost:8080/docs"],
        )


class ScanStreamTest(unittest.TestCase):
    def test_end_of_buffer_deferred(self):
        # 触及缓冲末尾的半截 URL 推迟判定
        self.assertEqual(scan_stream_urls("见 http://127.0.0.1:812", set()), [])

    def test_complete_url_detected(self):
        self.assertEqual(
            scan_stream_urls("见 http://127.0.0.1:8123/ 结束", set()),
            ["http://127.0.0.1:8123"],
        )

    def test_seen_filtered(self):
        self.assertEqual(
            scan_stream_urls("http://127.0.0.1:8123/ ", {"http://127.0.0.1:8123"}),
            [],
        )


class NormalizeSelfHealTest(unittest.TestCase):
    """老对话存量垃圾条目在读出时自愈（截回干净形态/剔除/重新去重）。"""

    def test_dirty_entries_healed(self):
        entries = [
            {"type": "file", "path": "cache/demo/index.html", "label": "index.html", "source": "file_edit"},
            {"type": "server", "url": "http://127.0.0.1:8321/index.html", "origin": "http://127.0.0.1:8321",
             "label": ":8321/index.html", "source": "command_output"},
            {"type": "server", "url": "http://127.0.0.1:8321/favicon.ico:0", "origin": "http://127.0.0.1:8321",
             "label": ":8321/favicon.ico:0", "source": "command_output"},
            {"type": "server", "url": "http://127.0.0.1:8321/index.html**", "origin": "http://127.0.0.1:8321",
             "label": ":8321/index.html**", "source": "model_output"},
            {"type": "server", "url": "http://127.0.0.1:8321/index.html**（预览面板里也有）",
             "origin": "http://127.0.0.1:8321", "label": ":8321/index.html**（预览面板里也有）",
             "source": "model_output"},
            {"type": "server", "url": "http://127.0.0.1", "origin": "http://127.0.0.1",
             "label": ":80", "source": "command_output"},
        ]
        healed = _normalize(entries)
        urls = [e["url"] for e in healed if e["type"] == "server"]
        # 4 条脏 server 条目 → 合并为 1 条干净 URL；favicon 与裸主机被剔除
        self.assertEqual(urls, ["http://127.0.0.1:8321/index.html"])
        # 文件条目不受影响
        self.assertEqual([e["path"] for e in healed if e["type"] == "file"], ["cache/demo/index.html"])
        # label 重算为干净形态
        server = [e for e in healed if e["type"] == "server"][0]
        self.assertEqual(server["label"], ":8321/index.html")
        self.assertEqual(server["origin"], "http://127.0.0.1:8321")


class LabelTest(unittest.TestCase):
    def test_label_with_path(self):
        self.assertEqual(_server_label("http://127.0.0.1:8321/index.html"), ":8321/index.html")

    def test_label_root(self):
        self.assertEqual(_server_label("http://localhost:5173"), ":5173")

    def test_origin(self):
        self.assertEqual(_origin("http://127.0.0.1:8321/a/b"), "http://127.0.0.1:8321")


if __name__ == "__main__":
    unittest.main()
