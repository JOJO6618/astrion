"""Astrion headless 后端入口（Gateway + Runtime，无 Web 站点职责）。

与 `server.app`（full 形态）是**同一后端进程的两种启动方式**：同一 RuntimeService
单例、同一数据目录、同一端口（默认 8091）。区别仅在启动时挂载的路由面——
headless 只挂 CLI / 桌面 / API 客户端依赖的 API 蓝图：

    gateway_bp    : /api/runtime/sessions（CLI 主通道：创建会话 / 拉历史）
    tasks         : run.* 任务域（发消息 / 事件轮询 / 取消）
    status_bp     : /api/health 探活、/api/status、host_workspace 工作区管理
    approval_bp   : 工具 / 计划 / 提问三组审批（Runtime 人机交互契约）
    usage_bp      : /api/usage 配额查询

被砍的「web 站点职责」（静态页面、web 登录、旧版通道等）在本形态下访问返回 404；
GET / 返回形态告知页（避免浏览器打开时误判为服务故障）。

本入口与 full 形态共用 Host 初始化和后台服务，各自装配 Flask app。

启动：python -m server.headless_app [--path PATH] [--port 8091] [--thinking-mode] [--debug]
"""
from __future__ import annotations

import argparse
import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, request

from config import (
    DEFAULT_PROJECT_PATH,
    MAX_UPLOAD_SIZE,
    OUTPUT_FORMATS,
    WEB_SERVER_HOST,
    WEB_SERVER_PORT,
)

DEFAULT_PORT = WEB_SERVER_PORT

from server.host_startup import hold_backend_instance_lock, initialize_host, start_background_jobs
from server.model_catalog import model_catalog_bp
from server.gateway_api import gateway_bp
from server.tasks.web import get_tasks_blueprint
from server.status import status_bp
from server.chat.approval import approval_bp
from server.usage import usage_bp
from server.providers import providers_bp
from server.crosspet_settings import crosspet_settings_bp
from server.schedules import schedules_bp
from server.security import attach_security_hooks

HEADLESS_BLUEPRINTS = (
    ("gateway", gateway_bp),
    ("tasks", get_tasks_blueprint()),
    ("model_catalog", model_catalog_bp),
    ("status", status_bp),
    ("approval", approval_bp),
    ("usage", usage_bp),
    ("providers", providers_bp),
    ("crosspet_settings", crosspet_settings_bp),
    ("schedules", schedules_bp),
)

_HEADLESS_LANDING_HTML = (Path(__file__).resolve().parents[1] / "static" / "headless.html").read_text(encoding="utf-8")


def create_headless_app() -> Flask:
    """创建 headless 形态的 Flask app（最小 API 路由面）。"""
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE
    # Flask session 仍被 Bearer 通道的身份注入使用（请求内 session 对象），
    # 必须配置 SECRET_KEY；解析规则与 full 形态一致。
    _secret_key = os.environ.get("WEB_SECRET_KEY") or os.environ.get("SECRET_KEY")
    if not _secret_key:
        _secret_key = secrets.token_hex(32)
        print(f"{OUTPUT_FORMATS['warning']} WEB_SECRET_KEY 未设置，已生成临时密钥（重启后所有会话将失效）。")
    app.config['SECRET_KEY'] = _secret_key
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)
    app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get("WEB_COOKIE_SAMESITE", "Strict")
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    _cookie_secure_env = (os.environ.get("WEB_COOKIE_SECURE") or "").strip().lower()
    app.config['SESSION_COOKIE_SECURE'] = _cookie_secure_env in {"1", "true", "yes"}

    attach_security_hooks(app)

    for _name, bp in HEADLESS_BLUEPRINTS:
        app.register_blueprint(bp)

    # host 全局设置面（personalization / path-authorization）：视图函数定义在 chat_bp 模块内，
    # 装饰器已双通道化（web session 或 host Bearer 均可）；此处直接注册到 headless app，
    # URL 与 full 形态一致（Blueprint.route 只登记不包装，函数对象可安全复用）。
    from server.chat.settings import (
        get_personalization_settings,
        update_personalization_settings,
    )
    from server.chat.permission import (
        get_path_authorization,
        update_path_authorization,
    )
    from server.conversation import list_background_commands, list_sub_agents, list_conversation_versioning_checkpoints
    from server.workflow_page import api_list_workflows
    from server.chat.command_blocking import get_command_blocking, update_command_blocking

    app.add_url_rule('/api/personalization', view_func=get_personalization_settings, methods=['GET'])
    app.add_url_rule('/api/personalization', view_func=update_personalization_settings, methods=['POST'])
    app.add_url_rule('/api/path-authorization', view_func=get_path_authorization, methods=['GET'])
    app.add_url_rule('/api/path-authorization', view_func=update_path_authorization, methods=['POST'])
    app.add_url_rule('/api/command-blocking', view_func=get_command_blocking, methods=['GET'])
    app.add_url_rule('/api/command-blocking', view_func=update_command_blocking, methods=['POST'])
    # 子智能体/后台指令列表（/agents /tasks 面板数据源；conversation_id 走 query 显式指定）
    app.add_url_rule('/api/sub_agents', view_func=list_sub_agents, methods=['GET'])
    app.add_url_rule('/api/background_commands', view_func=list_background_commands, methods=['GET'])
    # 工作流库列表（/workflow）与版本回溯检查点（/rewind）
    app.add_url_rule('/api/workflows', view_func=api_list_workflows, methods=['GET'])
    app.add_url_rule(
        '/api/conversations/<conversation_id>/versioning/checkpoints',
        view_func=list_conversation_versioning_checkpoints,
        methods=['GET'],
    )

    @app.route('/')
    def headless_landing():
        return _HEADLESS_LANDING_HTML

    @app.errorhandler(404)
    def headless_not_found(_err):
        # 非 API 的 GET 路径（/new、/<conv_id> 等前端路由）统一回落告知页——
        # 对齐 full 形态的 SPA fallback 行为，避免浏览器打开收藏链接看到裸 404；
        # API 路径保持 JSON 404。
        if request.method == 'GET' and not request.path.startswith('/api/'):
            return _HEADLESS_LANDING_HTML
        return jsonify({"success": False, "error": "not found"}), 404

    return app


def run_headless(path: str, thinking_mode: bool = False, port: int = DEFAULT_PORT, debug: bool = False):
    """初始化并以 headless 形态运行（与 full 形态共享初始化与后台任务）。"""
    writer = hold_backend_instance_lock(use_reloader=debug)
    app = create_headless_app()
    # 与 full 形态一致：同机多实例时 Cookie 按端口隔离，避免互相覆盖
    app.config['SESSION_COOKIE_NAME'] = os.environ.get(
        "WEB_SESSION_COOKIE_NAME", f"agents_session_{port}"
    )
    if writer:
        initialize_host(app, path, thinking_mode)
        start_background_jobs()
    app.run(
        host=WEB_SERVER_HOST,
        port=port,
        debug=debug,
        use_reloader=debug,
        threaded=True,
        load_dotenv=False,
    )


def parse_arguments():
    parser = argparse.ArgumentParser(description="Astrion headless backend (Gateway + Runtime, API-only)")
    parser.add_argument(
        "--path",
        default=str(Path(DEFAULT_PROJECT_PATH).resolve()),
        help="默认工作区路径（仅作兜底，工作区可在客户端中管理）",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    parser.add_argument("--thinking-mode", action="store_true", help="启用思考模式")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    return parser.parse_args()


def main():
    args = parse_arguments()
    print(f"{OUTPUT_FORMATS['info']} 以 headless 形态启动（仅 API 路由面，无 Web 界面）...")
    run_headless(path=args.path, thinking_mode=args.thinking_mode, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
