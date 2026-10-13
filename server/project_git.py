"""Project Git history APIs. Browsing never creates execution resources."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from modules.project_git.discovery import discover, resolve_repository
from modules.project_git.history import branches, changed_files, comparison, file_diff, history
from modules.project_git.runner import GitQueryError
from server.auth_helpers import api_login_required, get_current_username, login_required
from server.context.workspace import request_identity, resolve_workspace
from server.project_git_review import launch_review, list_reviews
from server.security import rate_limited

project_git_bp = Blueprint("project_git", __name__)


@project_git_bp.route("/git")
@login_required
def git_page():
    return current_app.send_static_file("index.html")


def _scoped_workspace(username: str, workspace_id: str, identity) -> dict:
    if not isinstance(workspace_id, str) or not workspace_id:
        raise GitQueryError("缺少工作区身份。")
    workspace = resolve_workspace(username, workspace_id, identity=identity, prepare=False)
    return {"workspace_id": workspace_id,
            "label": getattr(workspace, "label", None) or workspace_id,
            "path": str(Path(workspace.project_path).resolve())}


def _repository(repo_id: str):
    username = get_current_username()
    identity = request_identity()
    payload = (request.get_json(silent=True) or {}) if request.method == "POST" else {}
    if not isinstance(payload, dict):
        raise GitQueryError("请求参数必须是对象。")
    workspace_id = request.args.get("workspace_id") or payload.get("workspace_id")
    workspace = _scoped_workspace(username, workspace_id, identity)
    repo, _workspace = resolve_repository([workspace], repo_id, workspace_id)
    return repo, username, workspace_id, identity


def _int_arg(name: str, default: int, maximum: int) -> int:
    try:
        return min(maximum, max(0, int(request.args.get(name, default))))
    except (TypeError, ValueError) as exc:
        raise GitQueryError("无效的分页参数。") from exc


def _comparison(repo: Path, payload=None):
    params = payload if payload is not None else request.args
    if any(not isinstance(params.get(key, ""), str) for key in ("sha", "parent", "base", "head")):
        raise GitQueryError("提交和分支参数必须是字符串。")
    return comparison(repo, sha=params.get("sha", ""), parent=params.get("parent", ""),
                      base=params.get("base", ""), head=params.get("head", "HEAD"))


@project_git_bp.errorhandler(ValueError)
def invalid_query(exc):
    return jsonify(success=False, error=str(exc)), 400


@project_git_bp.errorhandler(RuntimeError)
def busy_query(exc):
    return jsonify(success=False, error=str(exc)), 409


@project_git_bp.route("/api/project-git/repositories")
@api_login_required
def repositories():
    identity = request_identity()
    workspace = _scoped_workspace(get_current_username(), request.args.get("workspace_id"), identity)
    return jsonify(success=True, data=discover([workspace],
                                               refresh=request.args.get("refresh") == "1"))


@project_git_bp.route("/api/project-git/<repo_id>/branches")
@api_login_required
def repository_branches(repo_id):
    repo, *_ = _repository(repo_id)
    return jsonify(success=True, data=branches(repo))


@project_git_bp.route("/api/project-git/<repo_id>/history")
@api_login_required
def repository_history(repo_id):
    repo, *_ = _repository(repo_id)
    query = request.args.get("search", "")[:500]
    return jsonify(success=True, data=history(repo, request.args.get("ref", ""), query,
                                              _int_arg("offset", 0, 1000000), 40))


@project_git_bp.route("/api/project-git/<repo_id>/comparison")
@api_login_required
def repository_comparison(repo_id):
    repo, *_ = _repository(repo_id)
    compare = _comparison(repo)
    files = changed_files(repo, compare)
    return jsonify(success=True, data={**compare, "files": files,
                                        "additions": sum(f["additions"] for f in files),
                                        "deletions": sum(f["deletions"] for f in files)})


@project_git_bp.route("/api/project-git/<repo_id>/diff")
@api_login_required
def repository_diff(repo_id):
    repo, *_ = _repository(repo_id)
    compare = _comparison(repo)
    return jsonify(success=True, data=file_diff(repo, compare, request.args.get("path", ""),
                                                _int_arg("offset", 0, 10000000), 1200,
                                                _int_arg("context", 3, 100)))


@project_git_bp.route("/api/project-git/<repo_id>/reviews")
@api_login_required
def repository_reviews(repo_id):
    _repo, username, workspace_id, identity = _repository(repo_id)
    return jsonify(success=True, data=list_reviews(username, workspace_id, repo_id, identity))


@project_git_bp.route("/api/project-git/<repo_id>/reviews", methods=["POST"])
@api_login_required
@rate_limited("git_review_create", 15, 60, scope="user")
def create_review(repo_id):
    repo, username, workspace_id, identity = _repository(repo_id)
    compare = _comparison(repo, request.get_json(silent=True) or {})
    return jsonify(success=True, data=launch_review(username, workspace_id, repo_id, repo, compare, identity)), 202
