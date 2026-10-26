"""One-shot, AST-indexed extraction of existing task helpers without rewriting bodies.

Kept as a reproducible record of the mechanical move. Refuses to run twice.
"""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "server/chat_flow_task_main.py"
GROUPS = {
    "chat_flow_message_policy": ["_user_message_ui_defaults"],
    "chat_flow_versioning": ["_should_skip_versioning_for_message", "_prepare_hidden_versioning_baseline_for_first_input", "_record_hidden_versioning_checkpoint_after_run"],
    "chat_flow_notice_dispatch": ["_persist_and_echo_preceding_notice", "_dispatch_completion_user_notice"],
    "chat_flow_notice_state": ["_build_shared_waiting_payload", "_build_sub_agent_notice_text", "_collect_pending_completion_notices", "_rollback_completion_notice_marks", "_has_pending_completion_work", "_has_pending_workflow_notices"],
    "chat_flow_notice_poll": ["poll_completion_notifications", "poll_multi_agent_notifications"],
    "chat_flow_notice_idle": ["_dispatch_multi_agent_idle_messages", "_dispatch_multi_agent_idle_messages_sync"],
}


def main():
    text = SOURCE.read_text()
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if "_user_message_ui_defaults" not in functions:
        raise SystemExit("Already extracted; refusing to overwrite")
    modules = {name: module for module, names in GROUPS.items() for name in names}
    modules["handle_task_with_sender"] = "chat_flow_task_main"
    modules["_VALID_USER_MESSAGE_SOURCES"] = "chat_flow_message_policy"
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) and not
               (isinstance(n, ast.ImportFrom) and n.module == "__future__")]
    constant = next(n for n in tree.body if isinstance(n, ast.Assign) and
                    any(isinstance(t, ast.Name) and t.id == "_VALID_USER_MESSAGE_SOURCES" for t in n.targets))

    def dependencies(nodes, module):
        used = {n.id for node in nodes for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        output = ["from __future__ import annotations\n"]
        for node in imports:
            selected = []
            for alias in node.names:
                bound = alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                if bound in used:
                    selected.append(alias)
            if selected:
                copy = ast.Import(names=selected) if isinstance(node, ast.Import) else ast.ImportFrom(module=node.module, names=selected, level=node.level)
                output.append(ast.unparse(copy) + "\n")
        for name in sorted(used & modules.keys()):
            target = modules[name]
            if target == module or name == "handle_task_with_sender":
                continue
            output.append(f"from .{target} import {name}\n")
        return "".join(output) + "\n\n"

    for module, names in GROUPS.items():
        nodes = [functions[name] for name in names]
        if module == "chat_flow_message_policy":
            nodes.insert(0, constant)
        body = "\n\n".join("".join(lines[n.lineno - 1:n.end_lineno]).rstrip() for n in nodes) + "\n"
        if "handle_task_with_sender" in body:
            body = body.replace("            task_handle = asyncio.create_task(handle_task_with_sender(",
                                "            from .chat_flow_task_main import handle_task_with_sender\n            task_handle = asyncio.create_task(handle_task_with_sender(")
        destination = ROOT / "server" / f"{module}.py"
        if destination.exists():
            raise SystemExit(f"Refusing to overwrite {destination}")
        destination.write_text(dependencies(nodes, module) + body)

    main_node = functions["handle_task_with_sender"]
    main_body = "".join(lines[main_node.lineno - 1:main_node.end_lineno])
    reexports = "".join(f"from .{module} import {', '.join(names)}\n" for module, names in GROUPS.items())
    SOURCE.write_text(dependencies([main_node], "chat_flow_task_main") + reexports + "\n\n" + main_body)


if __name__ == "__main__":
    main()
