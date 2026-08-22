"""Web server package entry.

使用 PEP 562 惰性导入：避免 `python -m server.app` 时因包级提前 import
导致 'server.app' 被加载两次（runpy RuntimeWarning）。
"""
import importlib

__all__ = ["app", "socketio", "run_server", "parse_arguments", "initialize_system", "resource_busy_page"]


def __getattr__(name):
    if name in __all__:
        module = importlib.import_module(".app", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
