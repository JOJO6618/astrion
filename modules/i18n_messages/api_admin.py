"""Backend i18n message pack: server/admin.py user-visible messages.

Domain prefix: admin. 保留 Host 管理员页（二级密码 / 自定义工具 / MCP）；
旧 Web 租户管理（邀请码、普通账号密码重置、API 用户）随 Docker fork 移出。
"""

MESSAGES = {
    "admin.secondary_required_error": {
        "zh-CN": "需要二级密码",
        "en-US": "Secondary password required",
    },
    "admin.enter_secondary_password": {
        "zh-CN": "请输入二级密码",
        "en-US": "Please enter the secondary password",
    },
    "admin.secondary_password_wrong": {
        "zh-CN": "二级密码错误",
        "en-US": "Incorrect secondary password",
    },
    "admin.missing_id": {
        "zh-CN": "缺少 id",
        "en-US": "Missing id",
    },
    "admin.tool_not_found": {
        "zh-CN": "未找到该工具",
        "en-US": "Tool not found",
    },
    "admin.missing_id_or_name": {
        "zh-CN": "缺少 id 或 name",
        "en-US": "Missing id or name",
    },
    "admin.tool_dir_missing": {
        "zh-CN": "工具不存在",
        "en-US": "Tool does not exist",
    },
    "admin.file_not_found": {
        "zh-CN": "文件不存在",
        "en-US": "File not found",
    },
    "admin.server_not_found": {
        "zh-CN": "未找到该服务",
        "en-US": "Server not found",
    },
}