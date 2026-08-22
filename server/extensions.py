"""Flask/SocketIO 扩展实例。"""
from flask_socketio import SocketIO

# 统一的 SocketIO 实例，使用线程模式以兼容现有逻辑
socketio = SocketIO(cors_allowed_origins="*", async_mode='threading', logger=False, engineio_logger=False)

__all__ = ["socketio"]
