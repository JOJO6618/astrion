"""tasks 蓝图对象（仅创建，不挂载路由）。

路由模块（api/skills）通过 `from server.tasks.blueprint import tasks_bp`
装饰挂载；Flask app 初始化时由 server/tasks/web.py 统一装配。
独立 Gateway/Runtime 进程无需加载本模块。
"""
from flask import Blueprint

tasks_bp = Blueprint("tasks", __name__)
