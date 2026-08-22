"""界面展示与日志配置。"""

OUTPUT_FORMATS = {
    "thinking": "💭 [思考]",
    "action": "🔧 [执行]",
    "file": "📁 [文件]",
    "search": "🔍 [搜索]",
    "code": "💻 [代码]",
    "terminal": "⚡ [终端]",
    "memory": "📝 [记忆]",
    "success": "✅ [成功]",
    "error": "❌ [错误]",
    "warning": "⚠️  [警告]",
    "confirm": "❓ [确认]",
    "info": "ℹ️  [信息]",
    "session": "📺 [会话]",
}

AGENT_VERSION = "v6.1"

LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

__all__ = [
    "OUTPUT_FORMATS",
    "AGENT_VERSION",
    "LOG_LEVEL",
    "LOG_FORMAT",
]
