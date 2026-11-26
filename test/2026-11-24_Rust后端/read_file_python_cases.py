"""Refresh Python read_file snapshots with --write. Run without arguments to check."""

import ast
import __future__
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = Path(__file__).with_name("read_file_fixtures.json")


def parsed(relative_path):
    path = ROOT / relative_path
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def values_from(relative_path, select):
    values = {}
    for node in parsed(relative_path).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if select(name):
                values[name] = eval(compile(ast.Expression(node.value), relative_path, "eval"), {}, values)
    return values


def load_methods(relative_path, namespace, names=None):
    source = next(node for node in parsed(relative_path).body if isinstance(node, ast.ClassDef))
    if names is not None:
        source.body = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    section = ast.Module(body=[source], type_ignores=[])
    exec(compile(section, relative_path, "exec", flags=__future__.annotations.compiler_flag), namespace)
    return namespace[source.name]


def generate():
    messages = {}
    for name in ("file_manager", "tools_read"):
        values = values_from(f"modules/i18n_messages/{name}.py", lambda key: key == "MESSAGES")
        messages.update(values["MESSAGES"])

    def translate(key, **kwargs):
        return messages[key]["zh-CN"].format(**kwargs)

    limits = values_from("config/limits.py", lambda name: name == "MAX_READ_FILE_CHARS" or name.startswith("READ_TOOL_"))
    namespace = {**limits, "tr": translate, "files_for_owner": lambda owner: owner}
    # Keep the actual handler and text methods. Supply only the trusted host-path
    # adapter and the context hook so this remains an isolated text parity test.
    sys.modules["modules.git_worktree.paths"] = types.SimpleNamespace(result_path=lambda _owner, path: path)
    tool = load_methods("core/main_terminal_parts/tools_read.py", namespace, {
        "_clamp_int", "_parse_optional_line", "_truncate_text_block", "_limit_text_chunks", "_handle_read_tool",
    })
    reader = load_methods("modules/file_manager/read_mixin.py", namespace)

    class Owner(tool, reader):
        context_manager = types.SimpleNamespace(load_file=lambda *args, **kwargs: None)

        def __init__(self, root):
            self.root = root

        def _validate_path(self, path):
            relative = Path(path)
            assert not relative.is_absolute() and ".." not in relative.parts
            return True, "", self.root / relative

        def _ensure_host_access(self, path, access):
            assert access == "read" and self.root in path.parents
            return True, ""

        def _native_open(self, path, mode, encoding):
            return path.open(mode, encoding=encoding)

        def _relative_path(self, path):
            return str(path.relative_to(self.root))

    files = {
        "sample.txt": "你好🦀\r\nLead\rHIT alpha\ntext\rhit β\r\n..\nCAP\nHIT z",
        "empty.txt": "",
        "separate.txt": "needle\n--\n--\nneedle\n--\n--\nneedle\n--\n--\nneedle\n--\n--\nneedle\n--\n--\nneedle\n",
    }
    cases = {
        "default_mode_and_newlines": {"path": "sample.txt"},
        "read_truncates_unicode": {"path": "sample.txt", "type": "READ", "max_chars": 3},
        "read_coerces_and_clips": {"path": "sample.txt", "type": "read", "start_line": "4", "end_line": 10},
        "read_exact_end": {"path": "sample.txt", "type": "read", "start_line": 1, "end_line": 1, "max_chars": 4},
        "read_empty_even_with_positive_bounds": {"path": "empty.txt", "type": "read", "start_line": 5, "end_line": 9},
        "read_bad_start": {"path": "sample.txt", "type": "read", "start_line": 0},
        "read_non_numeric_start": {"path": "sample.txt", "type": "read", "start_line": "one"},
        "read_reversed": {"path": "sample.txt", "type": "read", "start_line": 3, "end_line": 2},
        "read_out_of_file": {"path": "sample.txt", "type": "read", "start_line": 9},
        "empty_path": {"path": ""},
        "unknown_mode": {"path": "sample.txt", "type": "UNKNOWN"},
        "missing_file": {"path": "missing.txt", "type": "read"},
        "search_merges_overlapping_windows": {"path": "sample.txt", "type": "search", "query": "hit"},
        "search_caps_context_and_window_count": {"path": "sample.txt", "type": "search", "query": "HIT", "case_sensitive": True, "context_before": 12, "context_after": -1, "max_matches": 1},
        "search_default_window_limit": {"path": "separate.txt", "type": "search", "query": "needle"},
        "search_budget_across_windows": {"path": "sample.txt", "type": "search", "query": "HIT", "context_before": 0, "context_after": 0, "max_chars": 12},
        "search_no_regex": {"path": "sample.txt", "type": "search", "query": ".*"},
        "search_requires_query": {"path": "sample.txt", "type": "search", "query": ""},
        "extract_order_labels_and_clip": {"path": "sample.txt", "type": "extract", "segments": [{"label": "last", "start_line": 7, "end_line": 20}, {"start_line": 1, "end_line": 1}, {"label": "middle", "start_line": 3, "end_line": 4}]},
        "extract_budget_partial_segment": {"path": "sample.txt", "type": "extract", "max_chars": 5, "segments": [{"start_line": 1, "end_line": 1}, {"start_line": 2, "end_line": 2}, {"start_line": 3, "end_line": 3}]},
        "extract_budget_excludes_remaining_segments": {"path": "sample.txt", "type": "extract", "max_chars": 4, "segments": [{"start_line": 1, "end_line": 1}, {"start_line": 2, "end_line": 2}]},
        "extract_validates_all_despite_budget": {"path": "sample.txt", "type": "extract", "max_chars": 1, "segments": [{"start_line": 1, "end_line": 1}, {"start_line": 9, "end_line": 10}]},
        "extract_empty": {"path": "sample.txt", "type": "extract", "segments": []},
        "extract_missing_end": {"path": "sample.txt", "type": "extract", "segments": [{"start_line": 1}]},
        "extract_invalid_bounds": {"path": "sample.txt", "type": "extract", "segments": [{"start_line": 2, "end_line": 1}]},
        "extract_bad_item": {"path": "sample.txt", "type": "extract", "segments": [0]},
    }
    with tempfile.TemporaryDirectory(dir=SNAPSHOT.parent, prefix="read_file_case_") as temporary:
        owner = Owner(Path(temporary))
        for name, content in files.items():
            (owner.root / name).write_bytes(content.encode("utf-8"))
        expected = [dict(name=name, args=args, result=owner._handle_read_tool(args)) for name, args in cases.items()]
    return json.dumps(dict(files=files, cases=expected), ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    current = generate()
    if sys.argv[1:] == ["--write"]:
        SNAPSHOT.write_text(current, encoding="utf-8")
    elif sys.argv[1:] or SNAPSHOT.read_text(encoding="utf-8") != current:
        sys.exit("read_file Python snapshot differs or unexpected arguments")
    else:
        print("read_file Python snapshot verified")
