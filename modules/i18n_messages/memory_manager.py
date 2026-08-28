"""Backend i18n message pack: memory manager user-visible messages.

Pure data module — do not import anything here. Auto-discovered and merged
by modules/i18n.py at import time.
"""

MESSAGES = {
    "memory.append_requires_content": {
        "zh-CN": "append 需要 content",
        "en-US": "append requires content",
    },
    "memory.replace_requires_valid_index": {
        "zh-CN": "replace 需要有效的 index（从1开始）",
        "en-US": "replace requires a valid index (starting from 1)",
    },
    "memory.replace_requires_content": {
        "zh-CN": "replace 需要 content",
        "en-US": "replace requires content",
    },
    "memory.index_out_of_range": {
        "zh-CN": "序号 {index} 超出当前记忆条目数 {total}",
        "en-US": "Index {index} exceeds the current memory entry count {total}",
    },
    "memory.delete_requires_valid_index": {
        "zh-CN": "delete 需要有效的 index（从1开始）",
        "en-US": "delete requires a valid index (starting from 1)",
    },
    "memory.unknown_operation": {
        "zh-CN": "未知操作: {operation}",
        "en-US": "Unknown operation: {operation}",
    },
}