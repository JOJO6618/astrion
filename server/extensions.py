"""Flask/SocketIO 扩展实例。"""
import threading

from flask_socketio import SocketIO

# 统一的 SocketIO 实例，使用线程模式以兼容现有逻辑
socketio = SocketIO(cors_allowed_origins="*", async_mode='threading', logger=False, engineio_logger=False)


def emit_event(event, data, room=None, **kwargs):
    """Web 模式下经 socketio 实时推送；独立 Gateway 进程（socketio 未绑定 app）静默跳过。

    事件的权威记录是任务事件流（TaskRecord.events，_append_event 落盘在前）；
    socket 推送只是在线客户端的实时增量通道——独立进程没有 socket 客户端，
    跳过不丢数据。Web 模式下推送失败也静默（对齐原各调用点的 try/except 语义）。
    """
    if socketio.server is None:
        return
    try:
        socketio.emit(event, data, room=room, **kwargs)
    except Exception:
        pass


def run_background(fn, *args, **kwargs):
    """启动后台任务：Web 模式走 socketio.start_background_task（兼容其线程模型）；
    独立 Gateway 进程（socketio 未初始化）降级为 daemon 线程。"""
    if socketio.server is not None:
        return socketio.start_background_task(fn, *args, **kwargs)
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


__all__ = ["socketio", "emit_event", "run_background"]
