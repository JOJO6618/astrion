"""Generate or check snapshots from Python's actual native write/replace methods."""

import contextlib
import io
import json
import sys
import tempfile
from bisect import bisect_right
from pathlib import Path

from read_file_python_cases import load_methods, values_from

SNAPSHOT = Path(__file__).with_name("write_file_fixtures.json")


def generate():
    messages = values_from("modules/i18n_messages/file_manager.py", lambda key: key == "MESSAGES")["MESSAGES"]
    limits = values_from("config/limits.py", lambda key: key == "MAX_FILE_SIZE")

    def tr(key, **kwargs):
        return messages[key]["zh-CN"].format(**kwargs)

    namespace = {**limits, "tr": tr, "DISABLE_LENGTH_CHECK": True, "OUTPUT_FORMATS": {"file": "", "warning": ""},
                 "bisect_right": bisect_right}
    crud = load_methods("modules/file_manager/crud_mixin.py", namespace, {"write_file"})
    read = load_methods("modules/file_manager/read_mixin.py", namespace, {"_read_text_lines", "read_file"})
    replace = load_methods("modules/file_manager/replace_mixin.py", namespace, {
        "replace_many_in_file", "_find_match_line_numbers", "_find_match_contexts",
    })

    class Owner(crud, read, replace):
        def __init__(self, root):
            self.root = root

        def _validate_path(self, path):
            relative = Path(path)
            assert not relative.is_absolute() and ".." not in relative.parts
            return True, "", self.root / relative

        def _ensure_host_access(self, path, access):
            assert access in {"read", "write"} and self.root in path.parents
            return True, ""

        def _relative_path(self, path):
            return path.relative_to(self.root).as_posix()

        def _native_mkdir(self, path):
            path.mkdir(parents=True, exist_ok=True)

        def _native_read_text(self, path):
            return path.read_text(encoding="utf-8")

        def _native_open(self, path, mode, encoding):
            return path.open(mode, encoding=encoding)

    match = "line α\ntarget\nline β"
    repeated = f"top\nother\n{match}\nnear\nmedium\n{match}\nend\n"

    def group(old, new, **options):
        return dict(old_string=old, new_string=new, **options)

    def write(name, before, content, path="file.txt", **options):
        return dict(name=name, before=before, tool="write_file", args=dict(file_path=path, content=content, **options))

    def edit(name, before, *groups):
        return dict(name=name, before=before, tool="edit_file", args=dict(file_path="file.txt", replacements=list(groups)))

    cases = [
        write("create_unicode", None, "你好🦀\ntext"),
        write("create_nested_empty", None, "", path="nested/more/a.txt"),
        write("overwrite_universal_newlines", "旧\r\n内容\rtext", "新\r文本"),
        write("append_existing", "before\r\n", "added🦀\n", append=True),
        write("append_creates_a_new_file", None, "hello", append=True),
        write("append_empty", "before\n", "", append=True),
        edit("first_of_multiple", repeated, group(match, "line α\nchanged\nline β")),
        edit("replace_all", repeated, group(match, "line α\nchanged\nline β", replace_all=True)),
        edit("subsequent_group_observes_earlier_edit", repeated, group(match, "uno\ndos\ntres"), group("uno\ndos\ntres", "final")),
        edit("rollback_on_late_missing_match", repeated, group(match, "one\ntwo\nthree"), group("missing\nthree\nlines", "replacement")),
        edit("rollback_on_short_old_string", repeated, group(match, "one\ntwo\nthree"), group("one\ntwo\n", "replacement")),
        edit("replace_all_must_be_boolean", repeated, group(match, "replacement", replace_all="true")),
        edit("empty_old_string", repeated, group("", "replacement")),
        edit("identical_replacement", repeated, group(match, match)),
        edit("missing_new_string", repeated, dict(old_string=match)),
        edit("non_object_group", repeated, "replacement"),
        edit("old_string_starts_at_newline", "intro\r\nalpha\rbravo\r\ncharlie\r\nend", group("\nalpha\nbravo", "\njoined")),
        edit("python_unicode_splitlines", "start\nα\u2028β\x85γ\nend", group("α\u2028β\x85γ", "new")),
    ]
    for case in cases:
        with tempfile.TemporaryDirectory(dir=SNAPSHOT.parent, prefix="write_file_case_") as temporary:
            owner = Owner(Path(temporary))
            args = case["args"]
            path = owner.root / args["file_path"]
            if case["before"] is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(case["before"].encode("utf-8"))
            with contextlib.redirect_stdout(io.StringIO()):
                if case["tool"] == "write_file":
                    case["result"] = owner.write_file(args["file_path"], args["content"], "a" if args.get("append") else "w")
                else:
                    case["result"] = owner.replace_many_in_file(args["file_path"], args["replacements"])
            case["after"] = path.read_text(encoding="utf-8")
    return json.dumps(cases, ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    generated = generate()
    if sys.argv[1:] == ["--write"]:
        SNAPSHOT.write_text(generated, encoding="utf-8")
    elif SNAPSHOT.read_text(encoding="utf-8") != generated:
        sys.exit("write_file_fixtures.json differs from the Python implementation")
