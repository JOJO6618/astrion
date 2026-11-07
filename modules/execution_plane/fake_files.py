"""内存文件系统替身：``FakeExecutionBackend.files`` 的完整实现。

定位
----
- 与 ``LocalFileSystem`` 同签名、同"关键字段"，供 Runtime 在无真实副作用的前提
  下走到文件协议的每一条分支；
- 存储就是内存字典，**绝不**调用 ``Path.exists/resolve/read_text`` 等本地
  文件系统 API。元数据（size/created/modified/extension）全部由内存记录推导，
  不存在"用宿主 stat 冒充远端 stat"的路径。

与真实实现的差异（测试必须看得见，不允许被掩盖）
------------------------------------------------
- 不模拟 OS 权限、符号链接、大小写敏感、编码自动检测；
- ``workspace_root`` 默认 ``/workspace``，**仅是路径前缀语义**，机器上通常没有
  这个目录；相对 key（如 ``fake_probe.txt``）直接等价于
  ``/workspace/fake_probe.txt``；
- 文件内容按 UTF-8 存 bytes；非 UTF-8 内容只能经 ``read_binary`` 读取，文本
  接口返回固定错误，不做 latin-1 兜底；
- 错误文本是固定英文串，不透传 i18n 的 ``tr()`` 文案；
- 不落盘、无 fsync、无并发锁（单线程测试夹具）。

字典视图
--------
``FakeFileSystem`` 继承 ``dict``，为兼容既有断言
``backend.files.get("fake_probe.txt") == "line-a\\n"``：
字典 key 是规范化后的工作区相对路径，value 是原始 bytes；``get`` /
``__getitem__`` / ``values`` / ``items`` 对可解码内容返回 ``str``，解码失败时
返回 ``bytes``。需要确定拿到 bytes 请用 ``read_binary``。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .fake_files_meta import FakeFileMetaMixin
from .fake_files_read import FakeFileReadMixin
from .fake_files_write import FakeFileWriteMixin

DEFAULT_WORKSPACE_ROOT = "/workspace"


def _decode(value: bytes) -> Any:
    """字典视图：能按 UTF-8 解码就给 str，否则给 bytes（不吞异常）。"""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value


class FakeFileSystem(FakeFileReadMixin, FakeFileWriteMixin, FakeFileMetaMixin, dict):
    """内存文件系统（dict 子类；读/写/元数据分别由三个 mixin 提供）。

    Parameters
    ----------
    workspace_root:
        执行侧工作区前缀，默认 ``/workspace``。仅用于路径语义，不要求真实存在。
    platform:
        平台元数据（``sys.platform`` 风格的字符串等），只用于 ``inspect``/环境
        报告，不改变任何行为。
    clock:
        可注入的时钟（返回 epoch 秒），用于稳定 mtime 断言；默认 ``time.time``。
    """

    def __init__(
        self,
        workspace_root: str = DEFAULT_WORKSPACE_ROOT,
        *,
        platform: Optional[Dict[str, Any]] = None,
        clock: Optional[Any] = None,
    ) -> None:
        super().__init__()
        self.workspace_root = (workspace_root or DEFAULT_WORKSPACE_ROOT).rstrip("/") or "/"
        self.platform: Dict[str, Any] = dict(platform or {
            "platform": "fake",
            "system": "FakeOS",
            "machine": "fake-arch",
        })
        self._clock = clock or time.time
        self._dirs: set = set()          # 显式创建的目录（相对 key，根为 ""）
        self._meta: Dict[str, Dict[str, float]] = {}  # key -> {created, modified}

    # ------------------------------------------------------------------
    # dict 视图（只读语义；写入请走 write_file/create_file）
    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        norm, error = self._normalize(key)
        if error or norm not in self:
            return default
        return _decode(dict.__getitem__(self, norm))

    def __getitem__(self, key: str) -> Any:  # type: ignore[override]
        norm, error = self._normalize(key)
        if error or norm not in self:
            raise KeyError(key)
        return _decode(dict.__getitem__(self, norm))

    def values(self) -> List[Any]:  # type: ignore[override]
        return [_decode(v) for v in dict.values(self)]

    def items(self) -> List[Tuple[str, Any]]:  # type: ignore[override]
        return [(k, _decode(v)) for k, v in dict.items(self)]

    # ------------------------------------------------------------------
    # 内部：路径与元数据
    # ------------------------------------------------------------------
    def _normalize(self, path: Any) -> Tuple[str, Optional[str]]:
        """把调用方路径转成工作区相对 key。

        返回 ``(key, None)`` 或 ``("", error)``。拒绝 ``..`` 逃逸与工作区外的
        绝对路径——这与真实实现"不落到工作区外"的边界一致，但**不**代表真实
        实现的具体错误文案或 sandbox 策略。
        """
        if path is None:
            return "", "path is required"
        if not isinstance(path, str):
            return "", "path must be a string"
        raw = path.strip().replace("\\", "/")
        if raw in ("", "."):
            return "", None
        root = self.workspace_root
        if raw.startswith("/"):
            if raw == root:
                return "", None
            if root != "/" and raw.startswith(root + "/"):
                raw = raw[len(root) + 1:]
            else:
                return "", "path outside workspace"
        parts: List[str] = []
        for part in raw.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                return "", "path outside workspace"
            parts.append(part)
        return "/".join(parts), None

    def _now(self) -> float:
        return float(self._clock())

    def _touch(self, key: str) -> None:
        now = self._now()
        record = self._meta.setdefault(key, {"created": now, "modified": now})
        record["modified"] = now

    def _is_file(self, key: str) -> bool:
        return key in dict.keys(self)

    def _is_dir(self, key: str) -> bool:
        if key == "" or key in self._dirs:
            return True
        prefix = key + "/"
        return any(k.startswith(prefix) for k in dict.keys(self)) or any(
            d.startswith(prefix) for d in self._dirs
        )

    def _text(self, key: str) -> Optional[str]:
        try:
            return dict.__getitem__(self, key).decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _lines(self, key: str) -> List[str]:
        text = self._text(key)
        if text is None:
            return []
        return text.splitlines(keepends=True)

    def _meta_for(self, key: str) -> Dict[str, float]:
        record = self._meta.get(key)
        if record:
            return dict(record)
        return {"created": 0.0, "modified": 0.0}

    # ------------------------------------------------------------------
    # 测试辅助（不属于协议）
    # ------------------------------------------------------------------
    def seed(self, path: str, content: Any) -> "FakeFileSystem":
        """直接种一个文件（content 可为 str 或 bytes），供用例准备初态。

        与 ``write_file`` 不同：这里会补建缺失的父目录（测试夹具便利），
        但同样拒绝 ``..`` 逃逸与工作区外路径。
        """
        key, error = self._normalize(path)
        if error:
            raise ValueError(error)
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        now = self._now()
        parts = key.split("/")[:-1]
        for index in range(1, len(parts) + 1):
            self._dirs.add("/".join(parts[:index]))
        dict.__setitem__(self, key, data)
        self._meta[key] = {"created": now, "modified": now}
        return self

    def paths(self) -> List[str]:
        """当前全部文件 key（排序后的快照）。"""
        return sorted(dict.keys(self))

    def folder_paths(self) -> List[str]:
        return sorted(self._dirs)

    def snapshot(self) -> Dict[str, Any]:
        """可断言的纯 JSON 快照（不暴露 bytes 之外的对象）。"""
        return {
            "workspace_root": self.workspace_root,
            "files": {k: _decode(v) for k, v in dict.items(self)},
            "folders": sorted(self._dirs),
        }


__all__ = [
    "FakeFileSystem",
    "DEFAULT_WORKSPACE_ROOT",
    "FakeFileReadMixin",
    "FakeFileWriteMixin",
    "FakeFileMetaMixin",
]
