"""macOS/Windows 内置浏览器操作工具定义。

通用规则（标签范围、ref、快照差异格式、审批与安全）在冻结提示词 prompts/native_browser.txt，
这里只写各工具自身的参数与规则。仅受支持的桌面版 Agent 主对话注入；浏览器开关关闭时
工具仍在列表中（保持工具数组与前缀缓存稳定），调用会返回「浏览器未开启」。
"""
from typing import Any, Dict, List

_TAB = {"type": "string", "description": "目标标签短 ID（如 t2）；省略时使用你当前的标签"}
_TARGET = {"type": "string", "description": "快照中元素的 ref（如 e12）"}
_ELEMENT = {"type": "string", "description": "给用户看的元素描述（如「登录按钮」），用于显示与审批"}


def _tool(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}}


class ToolsDefinitionBrowserToolsMixin:
    def _build_browser_tools(self) -> List[Dict]:
        from modules.native_browser.availability import native_browser_supported
        if not native_browser_supported():
            return []
        intent = self._inject_intent
        return [
            _tool("browser_tabs", "管理本对话的浏览器标签：list 列出（含 ID、网址、标题、当前标签）；new 新建并设为当前（可带 url）；select 切换当前标签；close 关闭。", intent({
                "action": {"type": "string", "enum": ["list", "new", "select", "close"], "description": "操作"},
                "tab_id": {"type": "string", "description": "select/close 的目标标签 ID"},
                "url": {"type": "string", "description": "new 时要打开的 http/https 网址、本机绝对路径的 file:// 地址或搜索关键词（可选）；本地文件须在可读范围内，不支持远程 file 主机或 UNC 路径"},
            }), ["action"]),
            _tool("browser_navigate", "在标签中打开网址，或后退/前进/刷新。加载完成后返回完整快照。本对话没有标签时自动新建。", intent({
                "url": {"type": "string", "description": "http/https 网址或本机绝对路径的 file:// 地址；本地文件须在可读范围内，不支持远程 file 主机或 UNC 路径。也可传搜索关键词，使用用户设置的默认搜索引擎"},
                "action": {"type": "string", "enum": ["back", "forward", "reload"], "description": "不提供 url 时使用"},
                "tab_id": _TAB,
            }), []),
            _tool("browser_snapshot", "获取页面无障碍快照（含可操作元素的 ref）。默认整页完整快照；target 只看某个元素的子树，depth 限制层级。超长会截断。", intent({
                "tab_id": _TAB, "target": {"type": "string", "description": "只看该 ref 元素的子树（可选）"},
                "depth": {"type": "integer", "description": "最大层级（可选，1–100）"},
            }), []),
            _tool("browser_find", "在当前页面完整快照中查找文字（不区分大小写），返回匹配行及其最近的带 ref 父元素。页面很大时用它代替整页快照。", intent({
                "text": {"type": "string", "description": "要查找的文字"},
                "regex": {"type": "string", "description": "正则表达式（与 text 二选一）"},
                "tab_id": _TAB,
            }), []),
            _tool("browser_click", "点击元素（真实鼠标事件）。元素被遮挡时不会点击并说明遮挡物。", intent({
                "target": _TARGET, "element": _ELEMENT,
                "double_click": {"type": "boolean", "description": "是否双击"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "鼠标按键，默认 left"},
                "modifiers": {"type": "array", "items": {"type": "string", "enum": ["Alt", "Control", "Meta", "Shift"]}, "description": "按住的修饰键"},
                "tab_id": _TAB,
            }), ["target", "element"]),
            _tool("browser_type", "向输入框或可编辑区域输入文字（替换原有内容）。", intent({
                "target": _TARGET, "element": _ELEMENT,
                "text": {"type": "string", "description": "要输入的文字"},
                "submit": {"type": "boolean", "description": "输入后按回车"},
                "slowly": {"type": "boolean", "description": "逐字按键输入（用于依赖按键事件的输入框，较慢）"},
                "tab_id": _TAB,
            }), ["target", "element", "text"]),
            _tool("browser_fill_form", "一次填写多个表单字段。", intent({
                "fields": {"type": "array", "description": "字段列表", "items": {"type": "object", "properties": {
                    "target": _TARGET, "element": _ELEMENT,
                    "type": {"type": "string", "enum": ["textbox", "checkbox", "radio", "combobox", "slider"], "description": "字段类型"},
                    "value": {"type": "string", "description": "textbox 为文字；checkbox/radio 为 true/false；combobox 为选项值或文字；slider 为数值"},
                }, "required": ["target", "element", "type", "value"]}},
                "tab_id": _TAB,
            }), ["fields"]),
            _tool("browser_select_option", "在原生下拉框（select）中选择选项；自定义下拉请用 browser_click。", intent({
                "target": _TARGET, "element": _ELEMENT,
                "values": {"type": "array", "items": {"type": "string"}, "description": "选项的值或显示文字；多选框可给多个"},
                "tab_id": _TAB,
            }), ["target", "element", "values"]),
            _tool("browser_hover", "把鼠标移到元素上（用于展开悬停菜单、显示提示）。", intent({
                "target": _TARGET, "element": _ELEMENT, "tab_id": _TAB,
            }), ["target", "element"]),
            _tool("browser_drag", "从一个元素拖到另一个元素。", intent({
                "start_target": {"type": "string", "description": "起点元素 ref"},
                "start_element": {"type": "string", "description": "起点元素描述"},
                "end_target": {"type": "string", "description": "终点元素 ref"},
                "end_element": {"type": "string", "description": "终点元素描述"},
                "tab_id": _TAB,
            }), ["start_target", "start_element", "end_target", "end_element"]),
            _tool("browser_press_key", "按键或组合键，作用于当前焦点元素。如 Enter、Escape、ArrowDown、Tab、Meta+A、Control+Shift+K。", intent({
                "key": {"type": "string", "description": "键名或组合键"},
                "tab_id": _TAB,
            }), ["key"]),
            _tool("browser_scroll", "滚动页面或某个可滚动元素。", intent({
                "target": {"type": "string", "description": "在该 ref 元素上滚动（可选，默认整页）"},
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "description": "方向，默认 down"},
                "amount": {"type": "integer", "description": "像素数（可选，默认约一屏）"},
                "tab_id": _TAB,
            }), []),
            _tool("browser_wait_for", "等待一段时间，或等待某段文字出现/消失，然后返回页面变化。", intent({
                "time": {"type": "number", "description": "等待秒数（0–60）"},
                "text": {"type": "string", "description": "等待出现的文字"},
                "text_gone": {"type": "string", "description": "等待消失的文字"},
                "timeout": {"type": "number", "description": "等待文字的最长秒数，默认 10，最大 60"},
                "tab_id": _TAB,
            }), []),
            _tool("browser_take_screenshot", "截取页面图片，保存到工作区 .astrion/browser_screenshots/。只在需要看视觉效果（布局、样式、图片、图表）时使用；读取内容和操作页面用快照。", intent({
                "target": {"type": "string", "description": "只截该 ref 元素（可选）"},
                "full_page": {"type": "boolean", "description": "截整页（可选）"},
                "tab_id": _TAB,
            }), []),
            _tool("browser_handle_dialog", "处理网页弹出的 alert/confirm/prompt/离开页面确认对话框。", intent({
                "accept": {"type": "boolean", "description": "true 确认，false 取消/关闭"},
                "prompt_text": {"type": "string", "description": "prompt 对话框要填写的文字（可选）"},
                "tab_id": _TAB,
            }), ["accept"]),
            _tool("browser_file_upload", "为已打开的文件选择框提供文件；或用 target 直接给 <input type=file> 设置文件。paths 为空数组表示取消。", intent({
                "paths": {"type": "array", "items": {"type": "string"}, "description": "文件路径（工作区相对路径或可读的绝对路径）"},
                "target": {"type": "string", "description": "文件输入框的 ref（可选）"},
                "tab_id": _TAB,
            }), ["paths"]),
            _tool("browser_drop", "把文件或数据拖放到元素上（如拖拽上传区）。", intent({
                "target": _TARGET, "element": _ELEMENT,
                "paths": {"type": "array", "items": {"type": "string"}, "description": "要拖入的文件路径（可选）"},
                "data": {"type": "object", "description": "拖放数据，键为 MIME 类型（如 text/plain），值为内容（可选）", "additionalProperties": {"type": "string"}},
                "tab_id": _TAB,
            }), ["target", "element"]),
            _tool("browser_evaluate", "在页面中执行 JavaScript 函数并返回结果（可 JSON 序列化的值）。每次调用都需要审批，优先用其他工具。", intent({
                "function": {"type": "string", "description": "函数源码，如 () => document.title；指定 target 时为 (element) => ..."},
                "target": {"type": "string", "description": "作为参数传入的元素 ref（可选）"},
                "tab_id": _TAB,
            }), ["function"]),
            _tool("browser_fill_saved_login", "用浏览器已保存的账号密码填写当前页面的登录框（密码由浏览器直接填入，你看不到）。只在用户明确允许你代为登录时使用；每次都需要审批。填写后不会自动提交，需再点击登录按钮。网站保存了多个账号时须用 username 指定。", intent({
                "password_target": {"type": "string", "description": "密码输入框的 ref"},
                "username_target": {"type": "string", "description": "用户名/邮箱输入框的 ref（可选；登录页只有密码框时省略）"},
                "username": {"type": "string", "description": "要使用的已保存账号（可选；只有一个账号时可省略）"},
                "tab_id": _TAB,
            }), ["password_target"]),
            _tool("browser_console_messages", "读取本页加载以来的控制台消息（最多最近 200 条）。", intent({
                "level": {"type": "string", "enum": ["debug", "info", "warning", "error"], "description": "最低级别，默认 info"},
                "tab_id": _TAB,
            }), []),
            _tool("browser_network_requests", "列出本页加载以来的网络请求（默认不含图片、样式、脚本等静态资源）。", intent({
                "include_static": {"type": "boolean", "description": "是否包含静态资源"},
                "tab_id": _TAB,
            }), []),
            _tool("browser_network_request", "查看某个网络请求的详情（请求/响应头、请求体、响应体，敏感头已隐藏）。", intent({
                "id": {"type": "integer", "description": "browser_network_requests 返回的请求编号"},
                "tab_id": _TAB,
            }), ["id"]),
            _tool("browser_emulate_media", "模拟媒体特性（打印样式、深浅色、减少动画），只影响该标签。取值 default 表示恢复默认；未提供的参数恢复默认。", intent({
                "media": {"type": "string", "enum": ["screen", "print", "default"], "description": "媒体类型"},
                "color_scheme": {"type": "string", "enum": ["light", "dark", "no-preference", "default"], "description": "prefers-color-scheme"},
                "reduced_motion": {"type": "string", "enum": ["reduce", "no-preference", "default"], "description": "prefers-reduced-motion"},
                "tab_id": _TAB,
            }), []),
        ]
