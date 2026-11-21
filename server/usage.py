"""Compatibility endpoint for clients that query account quotas."""
from flask import Blueprint, jsonify

from .auth_helpers import api_login_required

usage_bp = Blueprint('usage', __name__)


@usage_bp.route('/api/usage', methods=['GET'])
@api_login_required
def get_usage_stats():
    return jsonify({"success": True, "applicable": False, "data": None})
