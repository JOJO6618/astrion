"""工作流页面路由 + REST API。

- `/workflows` 工作流库列表页，返回主 SPA 入口，由前端 bootstrapRoute 识别路径
- `/workflow/<name>` 工作流编辑器页，同上
- `/api/workflows` 系列：工作流 CRUD（WORKFLOW.md 落盘，见 modules/workflow_manager.py）
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from modules.workflow_manager import (
    delete_workflow,
    list_workflows,
    load_workflow,
    save_workflow,
)
from server.auth_helpers import api_login_required, login_required
from server.context import with_terminal

workflow_page_bp = Blueprint("workflow_page", __name__)


@workflow_page_bp.route("/workflows")
@login_required
def workflow_library_page():
    """工作流库入口，返回与 /new 相同的 SPA index.html。"""
    return current_app.send_static_file("index.html")


@workflow_page_bp.route("/workflow/<path:name>")
@login_required
def workflow_editor_page(name: str):
    """工作流编辑器入口，返回 SPA index.html 让前端路由处理。"""
    return current_app.send_static_file("index.html")


# ---------------------------------------------------------------- REST API


@workflow_page_bp.route("/api/workflows", methods=["GET"])
@api_login_required
@with_terminal
def api_list_workflows(terminal, workspace, username):
    """工作流列表（内置 + 用户库双源合并，仅元信息）。"""
    try:
        return jsonify({"workflows": list_workflows(workspace.data_dir)})
    except Exception as exc:
        return jsonify({"error": f"加载工作流列表失败：{exc}"}), 500


@workflow_page_bp.route("/api/workflows/<path:name>", methods=["GET"])
@api_login_required
@with_terminal
def api_load_workflow(name: str, terminal, workspace, username):
    """加载完整工作流定义（用户库优先，其次内置）。"""
    try:
        return jsonify({"workflow": load_workflow(name, workspace.data_dir)})
    except FileNotFoundError:
        return jsonify({"error": f"工作流不存在：{name}"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@workflow_page_bp.route("/api/workflows/<path:name>", methods=["PUT"])
@api_login_required
@with_terminal
def api_save_workflow(name: str, terminal, workspace, username):
    """保存工作流到用户库（原子写；结构 error 级校验不过则 400）。"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("workflow"), dict):
        return jsonify({"error": "请求体缺少 workflow 对象"}), 400
    wf = dict(data["workflow"])
    wf["name"] = name  # 名称以 URL 为准
    try:
        save_workflow(wf, workspace.data_dir)
        return jsonify({"ok": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"写入文件失败：{exc}"}), 500


@workflow_page_bp.route("/api/workflows/<path:name>", methods=["DELETE"])
@api_login_required
@with_terminal
def api_delete_workflow(name: str, terminal, workspace, username):
    """删除用户库中的工作流（内置示例不可删）。"""
    try:
        delete_workflow(name, workspace.data_dir)
        return jsonify({"ok": True})
    except FileNotFoundError:
        return jsonify({"error": f"工作流不存在：{name}"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"删除失败：{exc}"}), 500
