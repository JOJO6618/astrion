"""提供商与自定义模型管理 API（2026-09-24，opencode 模式）。

- ``/api/providers/*``：预置目录浏览、连接（API key + 自动 /models 发现）、
  刷新、断开。全部仅管理员可用（host 单用户 / docker 多用户均由管理员统一
  管理提供商，对齐 Codex 权限模型）；发现的模型对全员可见。
- ``/api/custom-models/*``：custom_models.json 的 UI 化 CRUD（「自定义」分组）。
  读取 = 当前生效文件（resolve_deploy_config 回退链）；写入 = 始终落部署目录
  ``DEPLOY_CONFIG_DIR/custom_models.json``——若生效文件是源码树种子，首次写入
  会把存量条目整体携带到部署目录（部署目录优先级高，内容等价不丢失）。

api_key 明文只存 ``<DATA_DIR>/providers.json``（0600），所有 API 输出仅掩码。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flask import Blueprint, jsonify, request

from config.paths import DEPLOY_CONFIG_DIR, resolve_deploy_config
from modules.provider_manager import get_provider_manager
from server.auth_helpers import admin_api_required, api_login_required

providers_bp = Blueprint("providers", __name__)


# ------------------------------------------------------------------ 提供商

@providers_bp.route("/api/providers/catalog", methods=["GET"])
@api_login_required
@admin_api_required
def providers_catalog():
    """预置目录 + 连接状态（提供商页主数据源）。"""
    manager = get_provider_manager()
    return jsonify({"success": True, "catalog": manager.catalog()})


@providers_bp.route("/api/providers", methods=["GET"])
@api_login_required
@admin_api_required
def providers_list():
    """已连接提供商列表（key 掩码）。"""
    return jsonify({"success": True, "providers": get_provider_manager().list_connected()})


@providers_bp.route("/api/providers/connect", methods=["POST"])
@api_login_required
@admin_api_required
def providers_connect():
    """连接提供商：目录条目 {catalog_id, api_key?} 或自定义 {provider_id, name, base_url, api_key?, headers?}。

    保存凭证后立即拉取 /models；模型拉取失败不影响连接本身（可稍后刷新重试）。
    """
    payload = request.get_json(silent=True) or {}
    result = get_provider_manager().connect(payload)
    status = 200 if result.get("success") else 400
    return jsonify(result), status


@providers_bp.route("/api/providers/<provider_id>/refresh", methods=["POST"])
@api_login_required
@admin_api_required
def providers_refresh(provider_id: str):
    """重新拉取该提供商的模型列表。"""
    result = get_provider_manager().refresh_models(provider_id)
    status = 200 if result.get("success") else 400
    return jsonify(result), status


@providers_bp.route("/api/providers/<provider_id>", methods=["DELETE"])
@api_login_required
@admin_api_required
def providers_disconnect(provider_id: str):
    """断开提供商：删除凭证与模型缓存，其模型立即从注册表消失。"""
    ok = get_provider_manager().disconnect(provider_id)
    if not ok:
        return jsonify({"success": False, "error": "provider_not_found"}), 404
    return jsonify({"success": True})


# ------------------------------------------------------------------ 自定义模型（custom_models.json UI 化）

_ALLOWED_CUSTOM_FIELDS = {
    "model_name", "display_name", "description", "model_description", "visible",
    "url", "apikey", "model_id", "multimodal", "reasoning_capability",
    "reasoning_effort", "context_window", "max_output_tokens",
    "thinkmode_status", "extra_parameter",
}


def _read_custom_models_raw() -> Tuple[List[Dict[str, Any]], Path]:
    """读当前生效 custom_models.json 的原始条目（不解析 ${ENV} 引用）。"""
    effective = Path(resolve_deploy_config("custom_models.json"))
    items: List[Dict[str, Any]] = []
    if effective.exists():
        try:
            data = json.loads(effective.read_text(encoding="utf-8"))
            raw_items = data if isinstance(data, list) else data.get("models", [])
            if isinstance(raw_items, list):
                items = [i for i in raw_items if isinstance(i, dict)]
        except Exception:
            items = []
    return items, effective


def _write_custom_models(items: List[Dict[str, Any]]) -> None:
    """写部署目录版本（不存在则创建；0600 因可能含明文 key）。"""
    target = Path(DEPLOY_CONFIG_DIR) / "custom_models.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"models": items}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)


def _sanitize_custom_model(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """白名单字段过滤 + 必填校验。返回 (条目, 错误消息)。"""
    item = {k: v for k, v in payload.items() if k in _ALLOWED_CUSTOM_FIELDS}
    model_name = str(item.get("model_name") or "").strip()
    if not model_name:
        return {}, "model_name_required"
    if any(ch in model_name for ch in "/\\ \t\n"):
        return {}, "model_name_invalid"
    if not str(item.get("url") or "").strip():
        return {}, "url_required"
    if not str(item.get("model_id") or "").strip():
        return {}, "model_id_required"
    if not str(item.get("apikey") or "").strip():
        return {}, "apikey_required"
    item["model_name"] = model_name
    return item, ""


@providers_bp.route("/api/custom-models", methods=["GET"])
@api_login_required
@admin_api_required
def custom_models_list():
    """自定义模型原始条目（apikey 可能含 ${ENV} 引用原文，前端原样展示编辑）。"""
    items, effective = _read_custom_models_raw()
    return jsonify({"success": True, "models": items, "source_path": str(effective)})


@providers_bp.route("/api/custom-models", methods=["POST"])
@api_login_required
@admin_api_required
def custom_models_create():
    payload = request.get_json(silent=True) or {}
    item, error = _sanitize_custom_model(payload)
    if error:
        return jsonify({"success": False, "error": error}), 400
    items, _ = _read_custom_models_raw()
    if any(str(i.get("model_name")) == item["model_name"] for i in items):
        return jsonify({"success": False, "error": "model_name_exists"}), 409
    items.append(item)
    try:
        _write_custom_models(items)
    except Exception as exc:
        return jsonify({"success": False, "error": f"write_failed: {exc}"}), 500
    return jsonify({"success": True, "model": item})


@providers_bp.route("/api/custom-models/<model_name>", methods=["PUT"])
@api_login_required
@admin_api_required
def custom_models_update(model_name: str):
    payload = request.get_json(silent=True) or {}
    item, error = _sanitize_custom_model(payload)
    if error:
        return jsonify({"success": False, "error": error}), 400
    items, _ = _read_custom_models_raw()
    index = next(
        (i for i, x in enumerate(items) if str(x.get("model_name")) == model_name), None
    )
    if index is None:
        return jsonify({"success": False, "error": "model_not_found"}), 404
    # model_name 改名时检查新名冲突
    if item["model_name"] != model_name and any(
        str(x.get("model_name")) == item["model_name"] for x in items
    ):
        return jsonify({"success": False, "error": "model_name_exists"}), 409
    items[index] = item
    try:
        _write_custom_models(items)
    except Exception as exc:
        return jsonify({"success": False, "error": f"write_failed: {exc}"}), 500
    return jsonify({"success": True, "model": item})


@providers_bp.route("/api/custom-models/<model_name>", methods=["DELETE"])
@api_login_required
@admin_api_required
def custom_models_delete(model_name: str):
    items, _ = _read_custom_models_raw()
    remaining = [x for x in items if str(x.get("model_name")) != model_name]
    if len(remaining) == len(items):
        return jsonify({"success": False, "error": "model_not_found"}), 404
    try:
        _write_custom_models(remaining)
    except Exception as exc:
        return jsonify({"success": False, "error": f"write_failed: {exc}"}), 500
    return jsonify({"success": True})
