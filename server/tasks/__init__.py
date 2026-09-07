# server/tasks/__init__.py - 任务核心层（无 Web 依赖，可独立加载）
#
# Gateway 化解耦（2026-09-07，G4）：本包 __init__ 只导出任务核心层
# （TaskManager 单例与 models 层符号），不创建 Flask Blueprint、
# 不 import 路由模块——保证 server.runtime 在无 Web 应用初始化的
# 进程中可独立加载使用。
# Web 路由装配入口：server/tasks/web.py 的 get_tasks_blueprint()
# （Flask app 初始化时由 server/app_legacy.py 显式调用）。
from server.tasks.models import *
from server.tasks.models import TaskManager

# 显式导出单例，便于旧代码导入
task_manager = TaskManager()
