"""安全与确认策略配置。"""

# 指令拦截由 modules.command_blocking 按当前用户配置处理。
# 此处仅保留路径与确认策略，不再加载部署级关键词。

FORBIDDEN_PATHS = [
    "/System",
    "/usr",
    "/bin",
    "/sbin",
    "/etc",
    "/var",
    "/tmp",
    "/Applications",
    "/Library",
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
]

FORBIDDEN_ROOT_PATHS = [
    "/",
    "C:\\",
    "~",
]

NEED_CONFIRMATION = [
    "delete_file",
    "delete_folder",
    "clear_file",
    "execute_terminal",
    "batch_delete",
]

__all__ = [
    "FORBIDDEN_PATHS",
    "FORBIDDEN_ROOT_PATHS",
    "NEED_CONFIRMATION",
]
