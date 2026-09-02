"""全局额度与工具限制配置。"""

import os

# 上下文与文件
MAX_CONTEXT_SIZE = 100000
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_OPEN_FILES = 20
MAX_UPLOAD_SIZE = 50 * 1024 * 1024

# 执行超时
CODE_EXECUTION_TIMEOUT = 60
TERMINAL_COMMAND_TIMEOUT = 120
SEARCH_MAX_RESULTS = 10

# 单条用户消息最大字符数（防成本攻击与内存放大；2026-09-02 安全审计新增）
MAX_MESSAGE_CHARS = int(os.environ.get("MAX_MESSAGE_CHARS", "200000") or 200000)

# 自动修复与工具调用限制（None 表示不限制）
AUTO_FIX_TOOL_CALL = False
AUTO_FIX_MAX_ATTEMPTS = 3
MAX_ITERATIONS_PER_TASK = None
MAX_CONSECUTIVE_SAME_TOOL = None
MAX_TOTAL_TOOL_CALLS = None
TOOL_CALL_COOLDOWN = 0.5

# 推理强度档位（模型配置 supports_reasoning_effort 时，思考模式下可选；None 表示默认不传参）
REASONING_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# 工具字符/体积限制
MAX_READ_FILE_CHARS = 50000
MAX_FOCUS_FILE_CHARS = 30000
MAX_RUN_COMMAND_CHARS = 50000
MAX_EXTRACT_WEBPAGE_CHARS = 80000

# read_file 子配置
READ_TOOL_MAX_FILE_SIZE = 100 * 1024 * 1024
READ_TOOL_DEFAULT_MAX_CHARS = MAX_READ_FILE_CHARS
READ_TOOL_DEFAULT_CONTEXT_BEFORE = 1
READ_TOOL_DEFAULT_CONTEXT_AFTER = 1
READ_TOOL_MAX_CONTEXT_BEFORE = 3
READ_TOOL_MAX_CONTEXT_AFTER = 5
READ_TOOL_DEFAULT_MAX_MATCHES = 5
READ_TOOL_MAX_MATCHES = 50

PROJECT_MAX_STORAGE_MB = int(os.environ.get("PROJECT_MAX_STORAGE_MB", "2048"))
PROJECT_MAX_STORAGE_BYTES = PROJECT_MAX_STORAGE_MB * 1024 * 1024

# 只读权限模式：run_command 命令白名单（启发式文本识别）。
# 注意（2026-08-30 起）：这只是「审批决策」与「兜底」的启发式，不再是安全边界——
# docker 只读由非特权 uid 内核 DAC 强制（modules/docker_readonly_exec.py），
# 宿主机只读由 OS 沙箱强制（modules/host_sandbox_runner.py）。已知可绕过
# （如 find . -delete），请勿再为它增加拦截规则试图把它做成边界。
READONLY_RUN_COMMAND_ALLOWED = (
    "grep",
    "find",
    "ls",
    "pwd",
    "tree",
    "cat",
    "head",
    "tail",
    "less",
    "rg",
    "wc",
    "du",
    "stat",
    "file",
    "sed",
    "awk",
    "git",
)
READONLY_RUN_COMMAND_ALLOWED_GIT_SUBCOMMANDS = (
    "status",
    "log",
    "diff",
    "show",
    "branch",
    "rev-parse",
)
READONLY_RUN_COMMAND_BLOCKED_TOKENS = (
    "&&",
    "||",
    ";",
    "|",
    ">",
    "<",
    "$(",
    "`",
)

__all__ = [
    "MAX_CONTEXT_SIZE",
    "MAX_FILE_SIZE",
    "MAX_OPEN_FILES",
    "MAX_UPLOAD_SIZE",
    "CODE_EXECUTION_TIMEOUT",
    "TERMINAL_COMMAND_TIMEOUT",
    "SEARCH_MAX_RESULTS",
    "AUTO_FIX_TOOL_CALL",
    "AUTO_FIX_MAX_ATTEMPTS",
    "MAX_ITERATIONS_PER_TASK",
    "MAX_CONSECUTIVE_SAME_TOOL",
    "MAX_TOTAL_TOOL_CALLS",
    "TOOL_CALL_COOLDOWN",
    "REASONING_EFFORT_LEVELS",
    "MAX_READ_FILE_CHARS",
    "MAX_FOCUS_FILE_CHARS",
    "MAX_RUN_COMMAND_CHARS",
    "MAX_EXTRACT_WEBPAGE_CHARS",
    "READ_TOOL_MAX_FILE_SIZE",
    "READ_TOOL_DEFAULT_MAX_CHARS",
    "READ_TOOL_DEFAULT_CONTEXT_BEFORE",
    "READ_TOOL_DEFAULT_CONTEXT_AFTER",
    "READ_TOOL_MAX_CONTEXT_BEFORE",
    "READ_TOOL_MAX_CONTEXT_AFTER",
    "READ_TOOL_DEFAULT_MAX_MATCHES",
    "READ_TOOL_MAX_MATCHES",
    "PROJECT_MAX_STORAGE_MB",
    "PROJECT_MAX_STORAGE_BYTES",
    "READONLY_RUN_COMMAND_ALLOWED",
    "READONLY_RUN_COMMAND_ALLOWED_GIT_SUBCOMMANDS",
    "READONLY_RUN_COMMAND_BLOCKED_TOKENS",
]
