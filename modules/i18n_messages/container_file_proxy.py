"""Backend i18n message pack: container file proxy user-visible messages.

Pure data module — do not import anything here. Auto-discovered and merged
by modules/i18n.py at import time.
"""

MESSAGES = {
    "container_proxy.container_not_ready": {
        "zh-CN": "容器未就绪，无法执行文件操作",
        "en-US": "Container not ready; cannot execute file operations",
    },
    "container_proxy.docker_runtime_not_found": {
        "zh-CN": "未找到 Docker 运行时",
        "en-US": "Docker runtime not found",
    },
    "container_proxy.exec_failed": {
        "zh-CN": "容器执行失败: {error}",
        "en-US": "Container execution failed: {error}",
    },
    "container_proxy.unknown_error": {
        "zh-CN": "未知错误",
        "en-US": "Unknown error",
    },
    "container_proxy.container_returned_error": {
        "zh-CN": "容器返回错误: {message}",
        "en-US": "Container returned an error: {message}",
    },
    "container_proxy.no_output": {
        "zh-CN": "容器未返回任何结果",
        "en-US": "Container returned no result",
    },
    "container_proxy.response_unparseable": {
        "zh-CN": "容器响应无法解析: {output}",
        "en-US": "Could not parse container response: {output}",
    },
}