"""Backend i18n message pack: preview panel API messages.

Pure data module — do not import anything here. Auto-discovered and merged
by modules/i18n.py at import time.
"""

MESSAGES = {
    "preview.invalid_key": {
        "zh-CN": "缺少有效的预览目标标识",
        "en-US": "Missing a valid preview target key",
    },
    "preview.file_unavailable": {
        "zh-CN": "文件不可用或不在可访问范围内",
        "en-US": "File is unavailable or outside the accessible scope",
    },
    "preview.file_read_failed": {
        "zh-CN": "读取文件失败",
        "en-US": "Failed to read the file",
    },
    "preview.target_not_registered": {
        "zh-CN": "该端口未登记为本对话的预览目标，拒绝代理",
        "en-US": "This port is not registered as a preview target for the current conversation; proxying refused",
    },
    "preview.server_unreachable": {
        "zh-CN": "无法连接到服务器（端口 {port}），它可能已停止运行",
        "en-US": "Cannot reach the server (port {port}); it may have stopped",
    },
    "preview.proxy_failed": {
        "zh-CN": "代理请求失败：{detail}",
        "en-US": "Proxy request failed: {detail}",
    },
    "preview.response_too_large": {
        "zh-CN": "响应体过大，已拒绝代理",
        "en-US": "Response body too large; proxying refused",
    },
    "preview.disabled_in_docker": {
        "zh-CN": "预览面板在 docker/web 模式下不可用（安全考虑，仅宿主机模式支持）",
        "en-US": "The preview panel is unavailable in docker/web mode (for security reasons; host mode only)",
    },
}
