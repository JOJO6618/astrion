# server/tasks/__init__.py - tasks_bp 兼容入口
from flask import Blueprint

tasks_bp = Blueprint("tasks", __name__)

from server.tasks.models import *
from server.tasks.skills import *
from server.tasks.helpers import *
from server.tasks.media import *

# 显式导出单例，便于旧代码导入；必须在 api 之前定义，api 会引用它
task_manager = TaskManager()

from server.tasks.api import *
