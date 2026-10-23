"""Child todo lists must never overwrite their parent's todo list."""
from modules.todo_manager import TodoManager


def execute_todo(store, tool_name, arguments):
    class Context:
        @property
        def todo_list(self):
            return store.get("todo")

        def set_todo_list(self, todo):
            store["todo"] = todo

    manager = TodoManager(Context())
    if tool_name == "todo_create":
        return manager.create_todo_list(overview=arguments.get("overview", ""), tasks=arguments.get("tasks", []))
    if tool_name == "todo_update_task":
        indices = arguments.get("task_indices", arguments.get("task_index"))
        return manager.update_task_status(task_indices=indices, completed=arguments.get("completed", True))
    return {"success": True, "todo_list": manager.get_snapshot()}
