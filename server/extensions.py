"""后台任务辅助（WebSocket 移除后仅剩后台线程启动器）。"""
import threading


def run_background(fn, *args, **kwargs):
    """以 daemon 线程启动后台任务。

    历史上该函数会在 Web 模式优先走 socketio.start_background_task；
    Socket.IO 移除后统一为 daemon 线程（与原降级路径行为一致）。
    """
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


__all__ = ["run_background"]
