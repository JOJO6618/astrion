# Shared fixtures for personal command blocking tests.
from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 隔离数据根：优先 ASTRION_DATA_ROOT（运行方注入的工作区 cache 临时 root），
# 否则退回系统临时目录。所有测试数据目录都建在该 root 之下，避免真实数据副作用。
_BASE_TMP = Path(os.environ.get("ASTRION_DATA_ROOT") or tempfile.gettempdir()) / "cb_tests"
_BASE_TMP.mkdir(parents=True, exist_ok=True)

# 推荐词仅用于模拟文本匹配，测试从不执行这些命令。
# 验证主动导入后仍保留大小写不敏感的子串匹配语义。
_LEGACY_WORD = "format"
# 个人规则专用词：保证不命中任何遗留引擎规则，只可能被个人拦截配置拦截。
_PERSONAL_WORD = "zzzpersonalban"
_PERSONAL_WORD_2 = "zzzsecondban"


def _import_cb(testcase: unittest.TestCase):
    """导入 modules.command_blocking；缺失即判契约未落地（测试失败而非跳过）。"""
    try:
        return importlib.import_module("modules.command_blocking")
    except Exception as exc:  # noqa: BLE001 - 需要把所有导入错误暴露为契约缺失
        testcase.fail(
            "modules/command_blocking.py 无法导入（契约尚未落地或实现有导入错误）: "
            f"{type(exc).__name__}: {exc}"
        )


def _cb_path(cb, data_dir) -> Path:
    # 契约公开 get_command_blocking_path；若实现另附别名也兼容。
    getter = getattr(cb, "get_command_blocking_path", None) or getattr(
        cb, "get_command_blocking_config_path")
    return Path(getter(str(data_dir)))


def _save_ok(testcase, cb, data_dir, payload) -> dict:
    """保存并断言成功；返回保存后的同结构配置 dict（容忍外层 success 包装）。"""
    result = cb.save_command_blocking_config(str(data_dir), payload)
    testcase.assertIsInstance(result, dict, f"save 应返回 dict: {result!r}")
    if result.get("success") is False or result.get("error"):
        testcase.fail(f"合法 payload 保存被拒绝: payload={payload!r} result={result!r}")
    if "rules" in result and "enabled" in result:
        return result
    for key in ("config", "data"):
        inner = result.get(key)
        if isinstance(inner, dict) and "rules" in inner and "enabled" in inner:
            return inner
    testcase.fail(f"save 返回值不是保存后的同结构配置: {result!r}")
    raise AssertionError("unreachable")


def _save_rejected(testcase, cb, data_dir, payload, what: str):
    """断言畸形 payload 被拒绝：抛异常或返回显式失败均可，唯独不能默默成功。"""
    try:
        result = cb.save_command_blocking_config(str(data_dir), payload)
    except Exception:
        return  # 抛异常是合法的拒绝方式
    if isinstance(result, dict) and (result.get("success") is False or result.get("error")):
        return
    testcase.fail(f"畸形 payload 未被拒绝（{what}）: payload={payload!r} -> {result!r}")


def _validate(testcase, cb, command: str, data_dir):
    """调用 validate_command 并校验返回结构 (bool, str)。"""
    outcome = cb.validate_command(command, str(data_dir))
    testcase.assertIsInstance(outcome, tuple, f"validate 应返回 tuple: {outcome!r}")
    testcase.assertEqual(len(outcome), 2, f"validate 应返回二元组: {outcome!r}")
    allowed, message = outcome
    testcase.assertIsInstance(bool(allowed), bool)
    return bool(allowed), (message if isinstance(message, str) else str(message or ""))


class _CommandBlockingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="case_", dir=str(_BASE_TMP)))
        self.data_dir = self.tmp / "user_a"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cb = _import_cb(self)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
