"""记忆文件配置。"""

from .paths import DATA_DIR

MAIN_MEMORY_FILE = f"{DATA_DIR}/memory.md"
TASK_MEMORY_FILE = f"{DATA_DIR}/task_memory.md"

__all__ = [
    "MAIN_MEMORY_FILE",
    "TASK_MEMORY_FILE",
]
