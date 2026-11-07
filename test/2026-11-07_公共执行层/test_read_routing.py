"""读取侧执行层迁移回归（2026-11-07）。

覆盖：
1. read_file 三模式经执行后端（`get_execution_backend(owner).files`），不再直连 file_manager；
2. 项目记忆检索的目录列举/内容读取经执行后端；
3. read_skill 的技能目录遍历经执行后端（同名冲突语义保持）；
4. ContextManager.load_file：整文件读取不重复读取；绑定后端失败不回退本机；
   无主终端时使用明确的本机 FileManager + LocalFileSystem；
5. prompt 的 AGENTS/CLAUDE 内容、mtime、一级子目录发现经执行后端元数据；
6. ProjectMixin 浅树经执行后端；元数据能力缺口不把空树写进对话历史；
7. 连接后端的失败不回退本机；容器元数据缺口只在本地后端回退旧元数据路径。

只使用临时目录与内存替身：不读真实 .env/settings.json，不启动服务。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 必须在导入 config / modules 之前隔离运行态。
from read_routing_env import prepare_isolated_runtime  # noqa: E402

_TEST_HOME = prepare_isolated_runtime("astrion_read_routing_")

from core.main_terminal_parts.context.prompt import PromptMixin  # noqa: E402,F401
from core.main_terminal_parts.tools_read import MainTerminalToolsReadMixin  # noqa: E402,F401
from utils.context_manager import ContextManager  # noqa: E402
from utils.context_manager.fs_access import (  # noqa: E402
    files_for_owner,
    inspect_meta,
)

# 共用替身（内存 backend + 隔离运行态），见 read_routing_fakes.py。
from read_routing_fakes import (  # noqa: E402
    METADATA_FAILURE as _METADATA_FAILURE,
    FakeBackend as _FakeBackend,
    FakeFiles as _FakeFiles,
    PoisonedFileManager as _PoisonedFileManager,
    ProjectStub as _ProjectStub,
    PromptStub as _PromptStub,
    ReadToolStub as _ReadToolStub,
    RecordingContextManager as _RecordingContextManager,
    RecordingConversationManager as _RecordingConversationManager,
)


class ReadToolRoutingTest(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeFiles(
            files={
                "notes/a.txt": "alpha\nbeta line\ngamma line\n",
                "notes/bom.txt": "no frontmatter\n",
            },
            listings={
                ".astrion/memory": {
                    "files": [{"name": "fix.md", "path": ".astrion/memory/fix.md"}],
                    "folders": [],
                },
            },
            metas={".astrion/memory": {"is_dir": True}},
        )
        self.fake.files[".astrion/memory/fix.md"] = (
            "---\nname: 修复流程\ndescription: 讨论公共执行层修复\n---\n\n"
            "关键词：公共执行层 修复\n正文其它内容\n"
        )
        self.stub = _ReadToolStub(self.fake)

    def test_read_mode_uses_backend_and_skips_local_reread(self):
        result = self.stub._handle_read_tool({"path": "notes/a.txt", "type": "read"})

        self.assertTrue(result["success"], result)
        self.assertEqual(result["content"], "alpha\nbeta line\ngamma line\n")
        # 只读一次：整文件内容直接交给上下文缓存
        self.assertEqual(len(self.fake.content_calls()), 1)
        self.assertEqual(self.fake.content_calls()[0][0], "read_text_segment")
        self.assertEqual(
            self.stub.context_manager.loaded,
            [("notes/a.txt", "alpha\nbeta line\ngamma line\n")],
        )

    def test_read_mode_segment_reloads_full_text_through_backend(self):
        result = self.stub._handle_read_tool(
            {"path": "notes/a.txt", "type": "read", "start_line": 2, "end_line": 2}
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["content"], "beta line\n")
        # 分段读取不重复读：load_file 由调用方传入 content=None，由
        # ContextManager 自己经同一执行后端补读整文件（见 ContextManagerLoadFileTest）。
        self.assertEqual(self.stub.context_manager.loaded, [("notes/a.txt", None)])
        self.assertEqual([call[0] for call in self.fake.content_calls()],
                         ["read_text_segment"])

    def test_search_mode_uses_backend(self):
        result = self.stub._handle_read_tool(
            {"path": "notes/a.txt", "type": "search", "query": "line", "max_matches": 1}
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["actual_matches"], 1)
        self.assertEqual(result["returned_matches"], 1)
        self.assertEqual([call[0] for call in self.fake.content_calls()], ["search_text"])

    def test_extract_mode_uses_backend(self):
        result = self.stub._handle_read_tool({
            "path": "notes/a.txt",
            "type": "extract",
            "segments": [{"start_line": 1, "end_line": 1}],
        })

        self.assertTrue(result["success"], result)
        self.assertEqual(result["segment_count"], 1)
        self.assertEqual(result["segments"][0]["content"], "alpha")
        self.assertEqual([call[0] for call in self.fake.content_calls()],
                         ["extract_segments"])

    def test_search_project_memory_uses_backend(self):
        result = self.stub._handle_search_project_memory(["修复"])

        self.assertTrue(result["success"], result)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["file"], "fix.md")
        self.assertEqual(result["results"][0]["name"], "修复流程")
        self.assertIn("list_files", [call[0] for call in self.fake.calls])
        self.assertIn("read_file", [call[0] for call in self.fake.calls])

    def test_search_project_memory_missing_dir_is_not_an_error(self):
        stub = _ReadToolStub(_FakeFiles(metas={}))
        result = stub._handle_search_project_memory(["任意"])

        self.assertTrue(result["success"], result)
        self.assertEqual(result["count"], 0)

    def test_read_skill_resolves_through_backend_catalog(self):
        fake = _FakeFiles(
            files={
                ".astrion/skills/beta/SKILL.md": "---\nname: Beta 技能\ndescription: b\n---\n内容",
                ".agents/skills/gamma/SKILL.md": "---\nname: Gamma 技能\n---\n内容",
                ".astrion/skills/alpha/SKILL.md": "---\nname: Alpha\n---\n内容",
                ".agents/skills/alpha/SKILL.md": "---\nname: Alpha dup\n---\n内容",
            },
            listings={
                ".astrion/skills": {
                    "folders": [{"name": "beta"}, {"name": "alpha"}, {"name": "bad name"}],
                    "files": [],
                },
                ".agents/skills": {
                    "folders": [{"name": "gamma"}, {"name": "alpha"}],
                    "files": [],
                },
            },
            metas={
                ".astrion/skills": {"is_dir": True},
                ".agents/skills": {"is_dir": True},
            },
        )
        stub = _ReadToolStub(fake)

        beta = stub._resolve_skill_id("beta")
        self.assertTrue(beta["success"], beta)
        self.assertEqual(beta["skill_id"], "beta")
        self.assertEqual(beta["display_dir"], ".astrion/skills")

        gamma = stub._resolve_skill_id("gamma")
        self.assertTrue(gamma["success"], gamma)
        self.assertEqual(gamma["display_dir"], ".agents/skills")

        conflict = stub._resolve_skill_id("alpha")
        self.assertFalse(conflict["success"])
        self.assertIn("alpha", conflict["error"])


class ContextManagerLoadFileTest(unittest.TestCase):
    def setUp(self):
        self.workspace = _TEST_HOME / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "notes").mkdir(exist_ok=True)
        (self.workspace / "notes" / "local.txt").write_text("LOCAL-CONTENT", encoding="utf-8")
        self.data_dir = _TEST_HOME / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def test_without_terminal_uses_explicit_local_instance(self):
        cm = ContextManager(str(self.workspace), data_dir=str(self.data_dir))

        self.assertTrue(cm.load_file("notes/local.txt"))
        self.assertEqual(cm.temp_files["notes/local.txt"], "LOCAL-CONTENT")
        # 明确的本机实例被缓存，可复用
        cached = getattr(cm, "_execution_local_files", None)
        self.assertIsInstance(cached, dict)
        self.assertIsNotNone(cached.get("access"))

    def test_missing_and_directory_are_rejected(self):
        cm = ContextManager(str(self.workspace), data_dir=str(self.data_dir))

        self.assertFalse(cm.load_file("notes/missing.txt"))
        self.assertFalse(cm.load_file("notes"))
        self.assertEqual(cm.temp_files, {})

    def test_bound_backend_reads_node_content_not_local(self):
        fake = _FakeFiles(files={"notes/local.txt": "REMOTE-CONTENT"})
        terminal = _ReadToolStub(fake, project_path=str(self.workspace))
        cm = ContextManager(str(self.workspace), data_dir=str(self.data_dir))
        cm.main_terminal = terminal

        self.assertTrue(cm.load_file("notes/local.txt"))
        self.assertEqual(cm.temp_files["notes/local.txt"], "REMOTE-CONTENT")
        # 不应因为“没找到”而另建本机实例
        self.assertIsNone(getattr(cm, "_execution_local_files", None))

    def test_bound_backend_failure_does_not_fall_back_to_local(self):
        fake = _FakeFiles(files={})  # 后端里没有该文件，但本机磁盘上有
        terminal = _ReadToolStub(fake, project_path=str(self.workspace))
        cm = ContextManager(str(self.workspace), data_dir=str(self.data_dir))
        cm.main_terminal = terminal

        self.assertFalse(cm.load_file("notes/local.txt"))
        self.assertEqual(cm.temp_files, {})
        self.assertIsNone(getattr(cm, "_execution_local_files", None))

    def test_caller_supplied_content_skips_read(self):
        fake = _FakeFiles(files={})
        terminal = _ReadToolStub(fake, project_path=str(self.workspace))
        cm = ContextManager(str(self.workspace), data_dir=str(self.data_dir))
        cm.main_terminal = terminal

        self.assertTrue(cm.load_file("notes/local.txt", content="GIVEN"))
        self.assertEqual(cm.temp_files["notes/local.txt"], "GIVEN")
        self.assertEqual(fake.content_calls(), [])


class PromptRoutingTest(unittest.TestCase):
    def _fake(self, *, metadata_failure=False):
        return _FakeFiles(
            files={"AGENTS.md": "# 项目规范\n", "CLAUDE.md": "claude rules\n"},
            listings={"": {
                "folders": [{"name": "src"}, {"name": "node_modules"},
                            {"name": "docs"}, {"name": ".git"}],
                "files": [{"name": "README.md", "path": "README.md", "size": 12}],
            }},
            metas={
                "AGENTS.md": {"is_file": True, "mtime": 1700000000.0, "size": 12},
                "src/AGENTS.md": {"is_file": True},
                "node_modules/AGENTS.md": {"is_file": True},
                "docs/AGENTS.md": {"is_file": False, "is_dir": False},
            },
            metadata_failure=metadata_failure,
        )

    def test_root_content_and_mtime_come_from_backend(self):
        stub = _PromptStub(self._fake())

        self.assertEqual(stub._load_agents_md_content(), "# 项目规范")
        updated_at = stub._load_agents_md_updated_at()
        self.assertIn("最后修改", updated_at)
        self.assertIn("2023-11-1", updated_at)

    def test_sub_agents_md_scan_uses_backend_and_skips_noise_dirs(self):
        stub = _PromptStub(self._fake())

        paths, total = stub._load_sub_agents_md_paths()
        self.assertEqual(paths, ["src/AGENTS.md"])
        self.assertEqual(total, 1)

    def test_missing_root_file_returns_none(self):
        stub = _PromptStub(_FakeFiles())
        self.assertIsNone(stub._load_root_md_content("AGENTS.md"))
        self.assertEqual(stub._load_root_md_updated_at("AGENTS.md"), "")
        self.assertEqual(stub._load_sub_agents_md_paths(), ([], 0))

    def test_metadata_failure_degrades_without_local_stat(self):
        stub = _PromptStub(self._fake(metadata_failure=True))

        # 内容仍读得到（内容类动作不依赖元数据）
        self.assertEqual(stub._load_agents_md_content(), "# 项目规范")
        # 元数据失败：按失败降级，不回退本机 Path.stat
        self.assertEqual(stub._load_agents_md_updated_at(), "")
        self.assertEqual(stub._load_sub_agents_md_paths(), ([], 0))


class ProjectStructureRoutingTest(unittest.TestCase):
    def _fake(self, *, metadata_failure=False):
        return _FakeFiles(
            files={},
            listings={"": {
                "folders": [{"name": "src", "path": "src"}],
                "files": [{"name": "README.md", "path": "README.md", "size": 2048}],
            }},
            metas={".astrion/skills": {"is_dir": True}},
            metadata_failure=metadata_failure,
        )

    def test_shallow_tree_uses_backend_listing(self):
        stub = _ProjectStub(self._fake(), annotations={"README.md": "说明"})

        structure = stub._get_project_structure_for_prompt()

        self.assertNotIn("listing_unavailable", structure)
        self.assertEqual([item["name"] for item in structure["folders"]],
                         ["src", "skills"])
        self.assertEqual(structure["folders"][1]["path"], ".astrion/skills")
        self.assertEqual(structure["files"],
                         [{"name": "README.md", "path": "README.md",
                           "size": 2048, "annotation": "说明"}])
        self.assertEqual(structure["total_files"], 1)
        self.assertEqual(structure["total_size"], 2048)
        self.assertIn("src", structure["tree"])
        self.assertEqual(structure["tree"]["README.md"]["annotation"], "说明")

        file_tree = stub._build_file_tree(structure)
        self.assertIn("src/", file_tree)
        self.assertIn("README.md", file_tree)

    def test_metadata_failure_does_not_persist_empty_snapshot(self):
        stub = _ProjectStub(self._fake(metadata_failure=True))

        snapshot = stub._ensure_project_snapshot()

        self.assertEqual(snapshot["statistics"]["total_files"], 0)
        self.assertEqual(stub.conversation_manager.snapshots, [])
        self.assertEqual(stub.conversation_metadata, {})

    def test_snapshot_persists_when_listing_is_available(self):
        stub = _ProjectStub(self._fake())

        snapshot = stub._ensure_project_snapshot()

        self.assertEqual(snapshot["statistics"]["total_files"], 1)
        self.assertEqual(len(stub.conversation_manager.snapshots), 1)
        self.assertEqual(stub.conversation_metadata["project_snapshot_at"],
                         snapshot["snapshot_at"])


class SingleFacePolicyTest(unittest.TestCase):
    """已绑定后端失败按失败处理：不换实现、不回退本机。"""

    def test_metadata_failure_is_propagated_verbatim(self):
        fake = _FakeFiles(metadata_failure=True)
        owner = _ReadToolStub(fake)

        access = files_for_owner(owner)
        result = inspect_meta(access, "AGENTS.md")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], _METADATA_FAILURE["error"])
        self.assertEqual(access.source, "backend:FakeBackend")

    def test_skill_catalog_failure_reports_error_instead_of_local_scan(self):
        fake = _FakeFiles(metadata_failure=True)
        stub = _ReadToolStub(fake)

        result = stub._resolve_skill_id("anything")

        self.assertFalse(result["success"])
        self.assertIn("执行后端", result["error"])

    def test_skill_catalog_missing_dirs_are_empty_not_errors(self):
        fake = _FakeFiles(
            listings={".astrion/skills": {"folders": [], "files": []}},
            metas={".astrion/skills": {"is_dir": True}},
        )
        stub = _ReadToolStub(fake)

        result = stub._resolve_skill_id("anything")

        self.assertFalse(result["success"])
        self.assertIn("anything", result["error"])


if __name__ == "__main__":
    unittest.main()
