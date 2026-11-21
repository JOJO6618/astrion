"""Schedule management: authenticated user scope, no foreground selection side effects."""
from dataclasses import asdict
from functools import wraps
import logging

from flask import Blueprint, jsonify, request

from modules.scheduled_tasks import get_service
from modules.scheduled_tasks.clock import next_dates
from modules.scheduled_tasks.schema import dump_definition, normalize_definition, parse_definition
from modules.scheduled_tasks.store import ConflictError
from server.auth_helpers import get_current_username, login_required
from server.gateway_auth import api_login_or_host_token_required

schedules_bp = Blueprint('schedules', __name__)
logger = logging.getLogger(__name__)


def require_host_identity():
    """只接受已认证的 Host 身份；历史普通用户／API 用户主体一律拒绝。"""
    from server.context.workspace import request_identity
    identity = request_identity()
    if not identity.host_mode or get_current_username() != "host":
        raise PermissionError("Host identity required")
    return identity


def principal():
    identity = require_host_identity()
    return {'username': get_current_username(), **asdict(identity)}


def payload():
    if request.content_length and request.content_length > 300000:
        raise ValueError('计划配置过大')
    result = request.get_json(silent=True)
    if not isinstance(result, dict):
        raise ValueError('请求必须是 JSON 对象')
    return result


def endpoint(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            require_host_identity()
            return jsonify({'success': True, 'data': function(*args, **kwargs)})
        except ConflictError as exc:
            return jsonify(success=False, error=str(exc)), 409
        except KeyError as exc:
            return jsonify(success=False, error=str(exc).strip("'")), 404
        except PermissionError as exc:
            return jsonify(success=False, error=str(exc)), 403
        except (ValueError, TypeError) as exc:
            return jsonify(success=False, error=str(exc)), 400
        except Exception:
            logger.exception('schedule API failed')
            return jsonify(success=False, error='定时任务服务暂时不可用'), 500
    return api_login_or_host_token_required(guarded)


@schedules_bp.get('/schedules')
@login_required
def page():
    from server.entry_assets import serve_entry_html
    return serve_entry_html('index.html')


@schedules_bp.get('/api/schedules')
@endpoint
def listing():
    service = get_service()
    enabled = request.args.get('enabled')
    return {'plans': service.list(get_current_username(), request.args.get('workspace_id'),
                                  None if enabled is None else enabled == 'true'), 'scheduler_error': service.error}


@schedules_bp.post('/api/schedules')
@endpoint
def submit():
    body = payload()
    if set(body) - {'definition', 'yaml', 'overwrite', 'workspace_id'}:
        raise ValueError('未知请求字段')
    if not isinstance(body.get('overwrite', False), bool):
        raise ValueError('overwrite 必须是布尔值')
    return get_service().submit(get_current_username(), body.get('yaml', body.get('definition')), principal(),
                                body.get('workspace_id'), overwrite=body.get('overwrite', False), source={'kind': 'page'})


@schedules_bp.post('/api/schedules/validate')
@endpoint
def validate():
    body = payload()
    value = body.get('yaml', body.get('definition'))
    if isinstance(value, str):
        value = parse_definition(value)
    definition = normalize_definition(value, body.get('workspace_id'))
    get_service().adapter.validate(definition, principal())
    return {'definition': definition, 'next_dates': next_dates(definition['schedule']),
            'yaml': dump_definition(definition)}


@schedules_bp.get('/api/schedules/options')
@endpoint
def options():
    from config.model_profiles import get_registered_model_profiles
    from modules.admin_policy_manager import get_effective_policy
    from modules.host_workspace_manager import load_host_workspace_catalog
    from zoneinfo import available_timezones
    p = principal()
    workspaces = [{'id': item['workspace_id'], 'label': item.get('label') or item['workspace_id']}
                  for item in load_host_workspace_catalog().get('workspaces', [])]
    # Host 没有普通用户记录或邀请码，策略按其显式 Host 角色解析。
    disabled = set(get_effective_policy(p['username'], p['role'], None).get('disabled_models') or [])
    models = [{'value': key, 'label': value.get('display_name') or value.get('name') or key,
               'thinking': bool(value.get('supports_thinking')), 'reasoning': bool(value.get('supports_reasoning_effort'))}
              for key, value in get_registered_model_profiles().items() if key not in disabled]
    return {'workspaces': workspaces, 'models': models, 'timezones': sorted(available_timezones()),
            'host_admin': p['role'] == 'admin'}


@schedules_bp.get('/api/schedules/<schedule_id>')
@endpoint
def detail(schedule_id):
    service = get_service()
    return service.serialize_plan(service.store.get(get_current_username(), schedule_id))


@schedules_bp.get('/api/schedules/<schedule_id>/yaml')
@endpoint
def yaml_export(schedule_id):
    return {'yaml': dump_definition(get_service().store.get(get_current_username(), schedule_id)['definition'])}


@schedules_bp.get('/api/schedules/<schedule_id>/runs')
@endpoint
def runs(schedule_id):
    service = get_service()
    service.store.get(get_current_username(), schedule_id, deleted=True)
    return [service.serialize_run(run, detail=True) for run in service.store.runs(get_current_username(), schedule_id, limit=100)]


@schedules_bp.post('/api/schedules/<schedule_id>/actions')
@endpoint
def action(schedule_id):
    body = payload()
    if set(body) - {'action', 'revision', 'run_id', 'request_id'}:
        raise ValueError('未知操作字段')
    return get_service().manage(get_current_username(), schedule_id, body.get('action'),
                                 **{key: body[key] for key in ('revision', 'run_id', 'request_id') if key in body})


@schedules_bp.post('/api/schedules/notifications/claim')
@endpoint
def claim():
    service = get_service()
    return [{**service.serialize_run(run), 'token': run['notification_token'],
             'name': run['definition']['name'], 'workspace_id': run['definition']['target']['workspace_id']}
            for run in service.store.claim_notifications(get_current_username())]


@schedules_bp.post('/api/schedules/notifications/<run_id>/ack')
@endpoint
def acknowledge(run_id):
    body = payload()
    token = body.get('token')
    if not isinstance(token, str) or not token:
        raise ValueError('缺少通知凭据')
    error = body.get('error')
    if error is not None and not isinstance(error, str):
        raise ValueError('error 必须是文字')
    return {'acknowledged': get_service().store.ack_notification(get_current_username(), run_id, token, error=error[:1000] if error else None)}
