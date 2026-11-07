"""工作区媒体读取经 selected backend.files（离线回归，不启动服务/模型/真实终端）。

覆盖：
1. Fake 后端路径在本机不存在时，仍按后端字节生成媒体引用与图片消息
   （tool_image_path → add_conversation → media_refs → _build_content_with_images 整条链）；
2. 后端读取失败不回退本机文件（本机同名文件不得被使用）；
3. 控制端 MediaStore 持久 blob 缓存保留：无后端时仍可从缓存重建图片消息；
4. OCRClient 走 backend.files.inspect_path + read_binary，而不是本地 read_bytes。

运行：
    python /opt/homebrew/bin/python3.11 test/2026-11-07_公共执行层/test_media_routing.py
"""
from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import ocr_client as ocr_module
from modules.ocr_client import OCRClient
from utils.context_manager import ContextManager

REMOTE_BYTES = b"\x89PNG\r\n\x1a\nremote-image-bytes"
LOCAL_BYTES = b"\x89PNG\r\n\x1a\nLOCAL-SHOULD-NOT-BE-USED"


class StubFiles:
    """执行后端文件视图替身：只认内存条目，绝不访问本机磁盘。"""

    def __init__(self, entries: dict, *, fail_read=(), fail_inspect=()) -> None:
        self.entries = entries
        self.fail_read = set(fail_read)
        self.fail_inspect = set(fail_inspect)
        self.calls: list = []

    def inspect_path(self, path: str, access: str = "read") -> dict:
        self.calls.append(("inspect", path, access))
        if path in self.fail_inspect:
            return {"success": False, "error": "backend-inspect-denied"}
        entry = self.entries.get(path)
        if entry is None:
            return {
                "success": True,
                "path": path,
                "relative_path": None,
                "exists": False,
                "is_file": False,
                "is_dir": False,
                "suffix": "",
                "size": None,
            }
        return {
            "success": True,
            "path": path,
            "relative_path": path,
            "exists": True,
            "is_file": True,
            "is_dir": False,
            "suffix": entry["suffix"],
            "size": len(entry["data"]),
        }

    def read_binary(self, path: str) -> bytes:
        self.calls.append(("read_binary", path))
        if path in self.fail_read:
            raise PermissionError("backend-read-denied")
        return self.entries[path]["data"]


class StubBackend:
    def __init__(self, files: StubFiles) -> None:
        self.files = files
        self.is_local = False
        self.supports_full_access = False


class FailingFiles:
    """任何读取都必须报错：用于证明不会悄悄改走别的实现。"""

    def __init__(self) -> None:
        self.calls: list = []

    def inspect_path(self, path: str, access: str = "read") -> dict:
        self.calls.append(("inspect", path, access))
        raise AssertionError("不应再访问执行后端")

    def read_binary(self, path: str) -> bytes:
        self.calls.append(("read_binary", path))
        raise AssertionError("不应再访问执行后端")


class MediaRoutingBase(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.data_dir = self.base / "data"
        self.data_dir.mkdir()

    def make_context(self, files: StubFiles) -> ContextManager:
        context = ContextManager(str(self.root), data_dir=str(self.data_dir))
        context.main_terminal = SimpleNamespace(execution_backend=StubBackend(files))
        return context

    @staticmethod
    def _parts(content):
        if isinstance(content, list):
            return content
        return [{"type": "text", "text": content}]

    @staticmethod
    def _image_payload(content):
        for part in MediaRoutingBase._parts(content):
            if part.get("type") == "image_url":
                url = str(part["image_url"]["url"])
                self_header, payload = url.split(",", 1)
                return self_header, base64.b64decode(payload)
        return None, None


class ContextMediaRoutingTests(MediaRoutingBase):
    def test_virtual_path_flows_through_backend_and_blob_cache(self):
        files = StubFiles({"virtual.png": {"data": REMOTE_BYTES, "suffix": ".png"}})
        context = self.make_context(files)
        # 本机存在同名文件：若控制端本地读，会拿到 LOCAL_BYTES
        (self.root / "virtual.png").write_bytes(LOCAL_BYTES)

        message = context.add_conversation(
            "tool",
            "图片已附加",
            tool_call_id="call_1",
            name="view_image",
            metadata={"tool_image_path": "virtual.png"},
            images=["virtual.png"],
        )
        refs = message.get("media_refs") or []
        self.assertEqual(len(refs), 1, message)
        self.assertEqual(context.media_store.load_bytes(refs[0]), REMOTE_BYTES)

        header, payload = self._image_payload(
            context._build_content_with_images("图片已附加", ["virtual.png"], [], media_refs=refs)
        )
        self.assertEqual(payload, REMOTE_BYTES)
        self.assertTrue(header.startswith("data:image/png"))

        # 无 media_refs 的历史路径分支同样走后端
        header, payload = self._image_payload(
            context._build_content_with_images("图片已附加", ["virtual.png"], [])
        )
        self.assertEqual(payload, REMOTE_BYTES)
        self.assertTrue(header.startswith("data:image/png"))

        self.assertIn(("read_binary", "virtual.png"), files.calls)

    def test_backend_read_failure_never_falls_back_to_local_file(self):
        files = StubFiles(
            {"virtual.png": {"data": REMOTE_BYTES, "suffix": ".png"}},
            fail_read={"virtual.png"},
        )
        context = self.make_context(files)
        (self.root / "virtual.png").write_bytes(LOCAL_BYTES)

        message = context.add_conversation(
            "tool",
            "读取失败",
            tool_call_id="call_2",
            name="view_image",
            metadata={"tool_image_path": "virtual.png"},
            images=["virtual.png"],
        )
        self.assertFalse(message.get("media_refs"), message)

        content = context._build_content_with_images("读取失败", ["virtual.png"], [])
        _, payload = self._image_payload(content)
        self.assertIsNone(payload, content)
        self.assertEqual(self._parts(content), [{"type": "text", "text": "读取失败"}])

    def test_backend_inspect_denied_is_not_local_checked(self):
        files = StubFiles(
            {"virtual.png": {"data": REMOTE_BYTES, "suffix": ".png"}},
            fail_inspect={"virtual.png"},
        )
        context = self.make_context(files)
        (self.root / "virtual.png").write_bytes(LOCAL_BYTES)

        message = context.add_conversation(
            "tool", "拒绝", tool_call_id="call_3", name="view_image",
            metadata={"tool_image_path": "virtual.png"}, images=["virtual.png"],
        )
        self.assertFalse(message.get("media_refs"), message)
        self.assertNotIn(("read_binary", "virtual.png"), files.calls)

    def test_persistent_blob_cache_survives_without_backend(self):
        files = StubFiles({"virtual.png": {"data": REMOTE_BYTES, "suffix": ".png"}})
        context = self.make_context(files)
        message = context.add_conversation(
            "tool", "缓存", tool_call_id="call_4", name="view_image",
            metadata={"tool_image_path": "virtual.png"}, images=["virtual.png"],
        )
        refs = message.get("media_refs") or []
        self.assertEqual(len(refs), 1, message)

        store_path = str(refs[0].get("store_path") or "")
        self.assertTrue(store_path.startswith("media_store/"))
        blob = context.media_store.conversations_dir / store_path
        self.assertTrue(blob.is_file(), blob)
        self.assertEqual(blob.read_bytes(), REMOTE_BYTES)

        failing = FailingFiles()
        context.main_terminal = SimpleNamespace(execution_backend=StubBackend(failing))
        header, payload = self._image_payload(
            context._build_content_with_images("缓存", ["virtual.png"], [], media_refs=refs)
        )
        self.assertEqual(payload, REMOTE_BYTES)
        self.assertEqual(failing.calls, [])

    def test_container_metadata_gap_still_reads_backend_bytes(self):
        class ContainerFiles(StubFiles):
            def inspect_path(self, path: str, access: str = "read") -> dict:
                self.calls.append(("inspect", path, access))
                return {"success": False, "code": "metadata_unsupported_in_container"}

        files = ContainerFiles({"clip.mp4": {"data": b"video-bytes", "suffix": ".mp4"}})
        context = self.make_context(files)
        message = context.add_conversation(
            "tool", "容器媒体", tool_call_id="call_5", name="view_video",
            metadata={"tool_video_path": "clip.mp4"}, videos=["clip.mp4"],
        )
        refs = message.get("media_refs") or []
        self.assertEqual(len(refs), 1, message)
        self.assertEqual(context.media_store.load_bytes(refs[0]), b"video-bytes")
        self.assertIn(("read_binary", "clip.mp4"), files.calls)

    def test_image_compression_reads_backend_bytes(self):
        try:
            import io as _io

            from PIL import Image
        except Exception:  # pragma: no cover - Pillow 是既有依赖，缺失时跳过
            self.skipTest("Pillow unavailable")
        buffer = _io.BytesIO()
        Image.new("RGB", (2000, 1500), (10, 20, 30)).save(buffer, format="PNG")
        source = buffer.getvalue()
        files = StubFiles({"big.png": {"data": source, "suffix": ".png"}})
        context = self.make_context(files)
        context.image_compression_mode = "540p"

        header, payload = self._image_payload(
            context._build_content_with_images("大图", ["big.png"], [])
        )
        self.assertTrue(header.startswith("data:image/png"))
        with Image.open(_io.BytesIO(payload)) as im:
            self.assertLessEqual(im.width, 960)
            self.assertLessEqual(im.height, 540)
        self.assertIn(("read_binary", "big.png"), files.calls)


class _FakeCompletions:
    def __init__(self) -> None:
        self.messages = None

    def create(self, **kwargs):
        self.messages = kwargs.get("messages")
        message = SimpleNamespace(content="一张测试图片")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeClient:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class OCRRoutingTests(MediaRoutingBase):
    def _ocr(self, files: StubFiles):
        backend = StubBackend(files)
        client = OCRClient(str(self.root), None, backend_getter=lambda: backend)
        client.model = "test-vlm-model"
        client._client_ready = True
        fake = _FakeClient()
        client.client = fake
        return client, fake

    def test_ocr_reads_binary_through_backend_for_virtual_path(self):
        files = StubFiles({"virtual.png": {"data": REMOTE_BYTES, "suffix": ".png"}})
        client, fake = self._ocr(files)
        (self.root / "virtual.png").write_bytes(LOCAL_BYTES)

        with patch.object(ocr_module, "OCR_API_KEY", "test-key"), \
             patch.object(ocr_module, "OCR_API_BASE_URL", "https://example.invalid/v1"):
            result = client.vlm_analyze("virtual.png", "描述图片")

        self.assertTrue(result["success"], result)
        self.assertEqual(result["content"], "一张测试图片")
        url = fake.completions.messages[0]["content"][0]["image_url"]["url"]
        header, payload = url.split(",", 1)
        self.assertTrue(header.startswith("data:image/png"))
        self.assertEqual(base64.b64decode(payload), REMOTE_BYTES)
        self.assertIn(("read_binary", "virtual.png"), files.calls)

    def test_ocr_read_failure_does_not_use_local_file(self):
        files = StubFiles(
            {"virtual.png": {"data": REMOTE_BYTES, "suffix": ".png"}},
            fail_read={"virtual.png"},
        )
        client, fake = self._ocr(files)
        (self.root / "virtual.png").write_bytes(LOCAL_BYTES)

        with patch.object(ocr_module, "OCR_API_KEY", "test-key"), \
             patch.object(ocr_module, "OCR_API_BASE_URL", "https://example.invalid/v1"):
            result = client.vlm_analyze("virtual.png", "描述图片")

        self.assertFalse(result["success"], result)
        self.assertIn("denied", result["error"])
        self.assertIsNone(fake.completions.messages)

    def test_ocr_denied_inspect_skips_read(self):
        files = StubFiles(
            {"virtual.png": {"data": REMOTE_BYTES, "suffix": ".png"}},
            fail_inspect={"virtual.png"},
        )
        client, fake = self._ocr(files)
        result = client.vlm_analyze("virtual.png", "描述图片")
        self.assertFalse(result["success"], result)
        self.assertEqual(result["error"], "backend-inspect-denied")
        self.assertNotIn(("read_binary", "virtual.png"), files.calls)
        self.assertIsNone(fake.completions.messages)


if __name__ == "__main__":
    unittest.main(verbosity=2)
