"""应用版本号。

用于对外 HTTP 请求的 User-Agent 标识（如 ``Astrion/1.0``）。
OpenCode 等供应商要求客户端工具以「产品名/版本号」格式自标识，
禁止使用宽泛 UA（如 python-httpx/x.y）。
"""

APP_VERSION = "1.0"
