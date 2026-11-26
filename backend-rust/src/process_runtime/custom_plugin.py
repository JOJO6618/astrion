"""Execute an installed Python tool with JSON arguments passed as values."""
import ast
import contextlib
import json
import re
import secrets
import string
import sys


MAX_INPUT = 2 * 1024 * 1024
MAX_OUTPUT = 24 * 1024
FORMATTER = string.Formatter()


class Capture:
    def __init__(self):
        self.parts = []
        self.size = 0
        self.truncated = False

    def write(self, text):
        available = MAX_OUTPUT - self.size
        if available:
            kept = text[:available]
            self.parts.append(kept)
            self.size += len(kept)
        self.truncated |= len(text) > available
        return len(text)

    def flush(self):
        pass

    def text(self):
        return "".join(self.parts)


def program(source, arguments):
    placeholders = {}
    prefix = "__astrion_parameter_" + secrets.token_hex(12) + "_"
    while prefix in source:
        prefix = "__astrion_parameter_" + secrets.token_hex(12) + "_"
    rendered = []
    for literal, field, spec, conversion in FORMATTER.parse(source):
        rendered.append(literal)
        if field is None:
            continue
        if field not in arguments:
            raise ValueError("Missing template parameter: " + field)
        if "{" in spec or "}" in spec or len(spec) > 20:
            raise ValueError("Invalid template format specifier")
        value = arguments[field]
        if conversion:
            value = FORMATTER.convert_field(value, conversion)
        if spec:
            value = FORMATTER.format_field(value, spec)
        key = prefix + str(len(placeholders)) + "__"
        placeholders[key] = value
        rendered.append(key)
    source = "".join(rendered)
    if not placeholders:
        return compile(source, "<custom tool>", "exec")
    pattern = re.compile("|".join(re.escape(key) for key in placeholders))

    class Bind(ast.NodeTransformer):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load) and node.id in placeholders:
                literal = ast.parse(repr(placeholders[node.id]), mode="eval").body
                return ast.copy_location(literal, node)
            return node

        def visit_Constant(self, node):
            if isinstance(node.value, str):
                value = pattern.sub(lambda match: str(placeholders[match.group()]), node.value)
                return ast.copy_location(ast.Constant(value), node)
            return node

    tree = Bind().visit(ast.parse(source, filename="<custom tool>", mode="exec"))
    return compile(ast.fix_missing_locations(tree), "<custom tool>", "exec")


def message(template, fields):
    if not isinstance(template, str) or not template.strip():
        return fields["output"]
    try:
        parts = []
        for literal, field, spec, conversion in FORMATTER.parse(template):
            parts.append(literal)
            if field is not None:
                if field not in fields or "{" in spec or "}" in spec or len(spec) > 20:
                    return fields["output"]
                value = fields[field]
                if conversion:
                    value = FORMATTER.convert_field(value, conversion)
                parts.append(FORMATTER.format_field(value, spec))
        return "".join(parts)[:MAX_OUTPUT]
    except (TypeError, ValueError):
        return fields["output"]


def main():
    line = sys.stdin.buffer.readline(MAX_INPUT + 1)
    if len(line) > MAX_INPUT:
        raise ValueError("Custom tool request exceeds the size limit")
    request = json.loads(line)
    output, stderr = Capture(), Capture()
    code = 0
    error = None
    try:
        namespace = {"__name__": "__main__", "arguments": request["arguments"]}
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(stderr):
            exec(program(request["source"], request["arguments"]), namespace)
    except SystemExit as stopped:
        code = stopped.code if isinstance(stopped.code, int) else (0 if stopped.code is None else 1)
        error = None if code == 0 else str(stopped)[:1000]
    except BaseException as failed:
        code, error = 1, str(failed)[:1000]
    text = output.text()
    truncate = request["return"].get("truncate")
    truncated = output.truncated or stderr.truncated
    if isinstance(truncate, int) and 0 < truncate < len(text):
        text = text[:truncate]
        truncated = True
    fields = {"output": text, "stderr": stderr.text(), "return_code": code, "tool_id": request["tool_id"]}
    fields.update(success=code == 0, custom_tool=True, truncated=truncated, error=error)
    fields["message"] = message(request["return"].get("template"), fields)
    print("\n" + request["nonce"] + " " + json.dumps(fields, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
