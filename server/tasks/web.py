"""tasks 包的 Web 路由装配入口（Flask app 初始化时调用）。

独立 Gateway/Runtime 进程不需要本模块——任务核心层（server.tasks
包的 task_manager 单例与 models 层）可脱离 Web 应用独立加载。
"""
from server.tasks.blueprint import tasks_bp


def get_tasks_blueprint():
    """返回已挂载全部路由的 tasks 蓝图。

    通过 import 路由模块触发 @tasks_bp.route 装饰器完成挂载；
    Python 模块只 import 一次，重复调用幂等。
    """
    from server.tasks import skills as _skills  # noqa: F401  （@tasks_bp.route 挂载）
    from server.tasks import helpers as _helpers  # noqa: F401  （保持历史装配行为等价）
    from server.tasks import media as _media  # noqa: F401  （保持历史装配行为等价）
    from server.tasks import api as _api  # noqa: F401  （@tasks_bp.route 挂载）
    return tasks_bp
