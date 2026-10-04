from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from command_blocking_helpers import (
    _CommandBlockingCase, _BASE_TMP, _LEGACY_WORD, _PERSONAL_WORD, _PERSONAL_WORD_2,
    _import_cb, _cb_path, _save_ok, _save_rejected, _validate,
)

class TestModulePathsAndDefaults(_CommandBlockingCase):

    def test_default_config_when_file_missing(self):
        cfg = self.cb.load_command_blocking_config(str(self.data_dir))
        self.assertIsInstance(cfg, dict)
        self.assertIs(cfg.get("enabled"), True, f"默认 enabled 应为 True: {cfg!r}")
        self.assertEqual(cfg.get("rules"), [], f"默认 rules 应为空列表: {cfg!r}")

    def test_path_is_data_dir_command_blocking_json(self):
        path = _cb_path(self.cb, self.data_dir)
        self.assertEqual(path.name, "command_blocking.json")
        self.assertEqual(path.parent, self.data_dir.resolve(),
                         "无符号链接时配置应落在 data_dir 下")

    def test_save_load_roundtrip_and_normalization(self):
        saved = _save_ok(self, self.cb, self.data_dir, {
            "enabled": True,
            "rules": ["  Foo  ", "FOO", "foo", "Bar"],
        })
        rules = saved.get("rules")
        self.assertIsInstance(rules, list)
        # trim + 大小写去重（保留首次出现），顺序无关断言
        self.assertEqual(sorted(r.lower() for r in rules), ["bar", "foo"],
                         f"大小写重复项应被去重: {rules!r}")
        for item in rules:
            self.assertEqual(item, item.strip(), f"规则应已 trim: {item!r}")
        self.assertIs(saved.get("enabled"), True)
        # load 回读一致
        loaded = self.cb.load_command_blocking_config(str(self.data_dir))
        self.assertEqual(loaded.get("rules"), rules)
        self.assertIs(loaded.get("enabled"), True)

    def test_whitespace_only_rules_dropped(self):
        saved = _save_ok(self, self.cb, self.data_dir, {
            "rules": ["   ", "", "\t\n", "keepme"],
        })
        self.assertEqual(saved.get("rules"), ["keepme"])

    def test_enabled_must_be_strict_bool(self):
        _save_ok(self, self.cb, self.data_dir, {"enabled": True, "rules": ["x"]})
        for bad in (1, 0, "true", "false", "yes", 1.0):
            _save_rejected(self, self.cb, self.data_dir, {"enabled": bad},
                           what=f"enabled={bad!r} 非严格 bool")
        loaded = self.cb.load_command_blocking_config(str(self.data_dir))
        self.assertIs(loaded.get("enabled"), True, "畸形 enabled 不应改动原配置")
        self.assertEqual(loaded.get("rules"), ["x"])

    def test_rules_items_must_be_str(self):
        _save_ok(self, self.cb, self.data_dir, {"rules": ["good"]})
        for bad_rules in ([123], [None], [True], [{"a": 1}], [["x"]], "notalist", 42):
            _save_rejected(self, self.cb, self.data_dir, {"rules": bad_rules},
                           what=f"rules={bad_rules!r} 含非 str 或非列表")
        loaded = self.cb.load_command_blocking_config(str(self.data_dir))
        self.assertEqual(loaded.get("rules"), ["good"], "畸形 rules 不应改动原配置")

    def test_non_dict_payload_rejected(self):
        for bad in (["enabled"], "enabled", 42, None, True):
            _save_rejected(self, self.cb, self.data_dir, bad, what=f"payload={bad!r}")


# ---------------------------------------------------------------------------
# 模块层：partial merge / 空列表合法 / 推荐不生效 / 实时切换
# ---------------------------------------------------------------------------

class TestModuleMergeAndSemantics(_CommandBlockingCase):

    def test_partial_merge_enabled_only_keeps_rules(self):
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_PERSONAL_WORD]})
        saved = _save_ok(self, self.cb, self.data_dir, {"enabled": False})
        self.assertIs(saved.get("enabled"), False)
        self.assertEqual(saved.get("rules"), [_PERSONAL_WORD],
                         "单字段开关（POST 语义）不得覆盖 rules")

    def test_partial_merge_rules_only_keeps_enabled(self):
        _save_ok(self, self.cb, self.data_dir, {"enabled": False})
        saved = _save_ok(self, self.cb, self.data_dir, {"rules": [_PERSONAL_WORD]})
        self.assertIs(saved.get("enabled"), False, "只更新 rules 不得改动 enabled")
        self.assertEqual(saved.get("rules"), [_PERSONAL_WORD])

    def test_empty_rules_is_legal_zero_bans_no_fallback(self):
        # 显式保存空列表：合法、0 禁令，且不得 fallback 到推荐词/遗留词
        saved = _save_ok(self, self.cb, self.data_dir,
                         {"enabled": True, "rules": []})
        self.assertEqual(saved.get("rules"), [])
        allowed, _ = _validate(self, self.cb, f"{_LEGACY_WORD} C: /q", self.data_dir)
        self.assertTrue(allowed, "空 rules 不得 fallback 拦截遗留匹配词")
        allowed2, _ = _validate(self, self.cb, "echo anything", self.data_dir)
        self.assertTrue(allowed2)

    def test_recommended_rules_not_auto_enabled(self):
        recommended = self.cb.get_recommended_rules()
        self.assertIsInstance(recommended, list)
        for item in recommended:
            self.assertIsInstance(item, str, f"推荐规则应为 str: {item!r}")
        # 文件缺失（默认配置）下，推荐词不得生效
        if recommended:
            word = recommended[0].strip()
            if word:
                allowed, _ = _validate(self, self.cb, f"echo {word}", self.data_dir)
                self.assertTrue(allowed,
                                f"推荐规则 {word!r} 不应在未导入时自动拦截")

    def test_validate_case_insensitive_substring(self):
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": ["Deploy PROD"]})
        allowed, msg = _validate(self, self.cb, "echo deploy prod now", self.data_dir)
        self.assertFalse(allowed, "大小写不敏感子串应命中")
        self.assertTrue(msg, "拦截时应返回非空原因")
        allowed2, _ = _validate(self, self.cb, "echo staging", self.data_dir)
        self.assertTrue(allowed2)

    def test_legacy_word_substring_semantics_after_import(self):
        # 遗留匹配词导入个人规则后仍按「子串 + 不区分大小写」原语义拦截：
        # 规则词是某更长单词的子串时，长词命令同样被拦。
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_LEGACY_WORD]})
        longer = _LEGACY_WORD + "ted"
        allowed, msg = _validate(self, self.cb, f"echo {longer.upper()} x", self.data_dir)
        self.assertFalse(allowed,
                         f"规则 {_LEGACY_WORD!r} 应以子串语义拦截包含词 {longer!r}")
        self.assertTrue(msg)

    def test_realtime_toggle_off_keeps_rules_and_reenable(self):
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_PERSONAL_WORD]})
        cmd = f"echo {_PERSONAL_WORD}"
        allowed, _ = _validate(self, self.cb, cmd, self.data_dir)
        self.assertFalse(allowed, "启用时应拦截")
        # 关闭：立即放行、规则保留（实时切换，无缓存）
        _save_ok(self, self.cb, self.data_dir, {"enabled": False})
        allowed, _ = _validate(self, self.cb, cmd, self.data_dir)
        self.assertTrue(allowed, "关闭后应立即放行")
        loaded = self.cb.load_command_blocking_config(str(self.data_dir))
        self.assertEqual(loaded.get("rules"), [_PERSONAL_WORD], "关闭不得清空规则")
        # 重新开启：规则仍在，立即再次拦截
        _save_ok(self, self.cb, self.data_dir, {"enabled": True})
        allowed, _ = _validate(self, self.cb, cmd, self.data_dir)
        self.assertFalse(allowed, "重新开启后规则应继续生效")


# ---------------------------------------------------------------------------
# 模块层：损坏配置 / 原子保存失败
# ---------------------------------------------------------------------------

class TestModuleFailureModes(_CommandBlockingCase):

    def _write_raw(self, data_dir: Path, text: str) -> Path:
        path = _cb_path(self.cb, data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_corrupt_config_validate_denies_with_error(self):
        path = self._write_raw(self.data_dir, "{ this is not json !!!")
        allowed, msg = _validate(self, self.cb, "echo hello", self.data_dir)
        self.assertFalse(allowed, "配置损坏时 validate 必须拒绝（fail-closed）")
        self.assertTrue(msg.strip(), "配置损坏拒绝时必须返回错误说明")
        # 不静默抹规则：validate/load 不得重写损坏文件
        self.assertEqual(path.read_text(encoding="utf-8"), "{ this is not json !!!")
        try:
            self.cb.load_command_blocking_config(str(self.data_dir))
        except Exception:
            pass  # load 允许抛错，但不允许改写文件
        self.assertEqual(path.read_text(encoding="utf-8"), "{ this is not json !!!",
                         "损坏配置不得被静默覆盖为默认")

    def test_atomic_save_failure_keeps_previous_config(self):
        good = _save_ok(self, self.cb, self.data_dir,
                        {"enabled": True, "rules": [_PERSONAL_WORD]})
        path = _cb_path(self.cb, self.data_dir)
        before = path.read_bytes()
        parent = path.parent
        original_mode = parent.stat().st_mode
        rejected = False
        try:
            os.chmod(parent, 0o555)  # 目录只读：临时文件创建/原子替换都应失败
            try:
                result = self.cb.save_command_blocking_config(
                    str(self.data_dir),
                    {"enabled": False, "rules": [_PERSONAL_WORD_2]},
                )
                if isinstance(result, dict) and (
                    result.get("success") is False or result.get("error")
                ):
                    rejected = True
            except Exception:
                rejected = True  # 抛异常是合法的失败方式
        finally:
            os.chmod(parent, original_mode)
        self.assertTrue(rejected, "写异常时保存不得报告成功")
        self.assertEqual(path.read_bytes(), before,
                         "保存失败不得破坏旧配置（原子写语义）")
        loaded = self.cb.load_command_blocking_config(str(self.data_dir))
        self.assertEqual(loaded.get("rules"), good.get("rules"))
        self.assertIs(loaded.get("enabled"), True)


# ---------------------------------------------------------------------------
# 模块层：符号链接共享 / 跨用户隔离
# ---------------------------------------------------------------------------

class TestModuleSharingAndIsolation(_CommandBlockingCase):

    def _make_workspace_with_shared_personalization(self, shared: Path, name: str) -> Path:
        ws = self.tmp / name
        ws.mkdir(parents=True, exist_ok=True)
        os.symlink(str(shared / "personalization.json"), str(ws / "personalization.json"))
        return ws

    def test_symlinked_personalization_shares_config_across_workspaces(self):
        shared = self.tmp / "shared_user_state"
        shared.mkdir(parents=True, exist_ok=True)
        (shared / "personalization.json").write_text("{}", encoding="utf-8")
        ws1 = self._make_workspace_with_shared_personalization(shared, "ws1")
        ws2 = self._make_workspace_with_shared_personalization(shared, "ws2")

        path1 = _cb_path(self.cb, ws1)
        path2 = _cb_path(self.cb, ws2)
        self.assertEqual(path1.resolve(), (shared / "command_blocking.json").resolve(),
                         "配置应落在 personalization.json 符号链接的真实父目录")
        self.assertEqual(path1.resolve(), path2.resolve(),
                         "同用户不同工作区应共享同一份配置")

        _save_ok(self, self.cb, ws1, {"enabled": True, "rules": [_PERSONAL_WORD]})
        self.assertTrue((shared / "command_blocking.json").exists(),
                        "保存应写穿符号链接到真实共享目录")
        loaded_ws2 = self.cb.load_command_blocking_config(str(ws2))
        self.assertEqual(loaded_ws2.get("rules"), [_PERSONAL_WORD],
                         "另一工作区应读到同用户共享的规则")
        allowed, _ = _validate(self, self.cb, f"echo {_PERSONAL_WORD}", ws2)
        self.assertFalse(allowed, "共享配置应在另一工作区同样拦截")

    def test_cross_user_isolation(self):
        user_b = self.tmp / "user_b"
        user_b.mkdir(parents=True, exist_ok=True)
        _save_ok(self, self.cb, self.data_dir,
                 {"enabled": True, "rules": [_PERSONAL_WORD]})
        _save_ok(self, self.cb, user_b,
                 {"enabled": True, "rules": [_PERSONAL_WORD_2]})
        path_a = _cb_path(self.cb, self.data_dir)
        path_b = _cb_path(self.cb, user_b)
        self.assertNotEqual(path_a.resolve(), path_b.resolve(),
                            "不同用户配置文件必须隔离")
        loaded_a = self.cb.load_command_blocking_config(str(self.data_dir))
        loaded_b = self.cb.load_command_blocking_config(str(user_b))
        self.assertEqual(loaded_a.get("rules"), [_PERSONAL_WORD])
        self.assertEqual(loaded_b.get("rules"), [_PERSONAL_WORD_2])
        # 用户 A 的规则不影响用户 B 的命令判定
        allowed_b, _ = _validate(self, self.cb, f"echo {_PERSONAL_WORD}", user_b)
        self.assertTrue(allowed_b, "其他用户的规则不得拦截本用户命令")
        allowed_a, _ = _validate(self, self.cb, f"echo {_PERSONAL_WORD}", self.data_dir)
        self.assertFalse(allowed_a)


# ---------------------------------------------------------------------------
# 执行入口：CommandMixin（TerminalOperator._validate_command 绑定 data_dir）
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
