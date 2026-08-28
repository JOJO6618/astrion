"""Backend i18n message pack: auth-family user-visible error messages.

Covers server/auth.py, server/api_auth.py, server/auth_helpers.py and
server/usage.py. Pure data module — do not import anything here.
Auto-discovered and merged by modules/i18n.py at import time.
zh-CN copy is verbatim from source; en-US is concise product-level
English (sentence case).
"""

MESSAGES = {
    # ── server/auth.py ──
    "auth.login_rate_limited": {
        "zh-CN": "登录请求过于频繁，请稍后再试。",
        "en-US": "Too many login attempts. Please try again later.",
    },
    "auth.register_rate_limited": {
        "zh-CN": "注册请求过于频繁，请稍后再试。",
        "en-US": "Too many registration attempts. Please try again later.",
    },
    "auth.too_many_attempts": {
        "zh-CN": "尝试次数过多，请 {seconds} 秒后重试。",
        "en-US": "Too many attempts. Please try again in {seconds} seconds.",
    },
    "auth.invalid_credentials": {
        "zh-CN": "账号或密码错误",
        "en-US": "Incorrect username or password",
    },
    "auth.host_mode_disabled": {
        "zh-CN": "宿主机模式未启用",
        "en-US": "Host mode is not enabled",
    },
    "auth.resource_busy": {
        "zh-CN": "资源繁忙，请稍后再试",
        "en-US": "Resources are busy. Please try again later",
    },
    "auth.not_logged_in": {
        "zh-CN": "未登录",
        "en-US": "Not logged in",
    },
    "auth.host_mode_tutorial_not_applicable": {
        "zh-CN": "宿主机模式无需设置新手教程状态",
        "en-US": "Tutorial status does not apply in host mode",
    },
    "auth.user_not_found": {
        "zh-CN": "用户不存在",
        "en-US": "User not found",
    },
    "auth.terminal_blocked_by_admin": {
        "zh-CN": "实时终端已被管理员禁用",
        "en-US": "Realtime terminal has been disabled by the administrator",
    },

    # ── server/api_auth.py ──
    "auth.missing_bearer_token": {
        "zh-CN": "缺少 Bearer Token",
        "en-US": "Missing Bearer Token",
    },
    "auth.invalid_token": {
        "zh-CN": "无效的 Token",
        "en-US": "Invalid Token",
    },

    # ── server/auth_helpers.py ──
    "auth.admin_required": {
        "zh-CN": "需要管理员权限",
        "en-US": "Administrator permission required",
    },

    # ── server/usage.py ──
    "auth.usage_user_not_found": {
        "zh-CN": "未找到用户",
        "en-US": "User not found",
    },
}