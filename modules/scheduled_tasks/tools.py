"""Agent tools. Identity comes from the bound terminal, never from model arguments."""
from . import get_service
from .schema import normalize_definition, parse_definition


def definitions():
    def tool(name, description, properties, required):
        return {'type': 'function', 'function': {'name': name, 'description': description,
                'parameters': {'type': 'object', 'properties': {'intent': {'type': 'string', 'description': '用不超过15字说明目的'}, **properties},
                               'required': ['intent', *required], 'additionalProperties': False}}}
    return [tool('submit_schedule', '读取 YAML 文件并验证保存定时 Agent 任务。先阅读 scheduled-tasks 技能；覆盖必须写明 id、revision 和 overwrite。',
                 {'file_path': {'type': 'string'}, 'overwrite': {'type': 'boolean', 'default': False}}, ['file_path']),
            tool('query_schedules', '查询当前用户的计划、配置与运行记录。支持工作区、启用状态、ID和字段投影，不枚举模型或工作区。',
                 {'schedule_id': {'type': 'string'}, 'workspace_id': {'type': 'string'}, 'enabled': {'type': 'boolean'},
                  'include_runs': {'type': 'boolean'}, 'fields': {'type': 'array', 'items': {'type': 'string'}}}, []),
            tool('manage_schedule', '开启、暂停、删除、立即运行计划或停止指定运行。暂停/删除不会停止已开始的运行。',
                 {'schedule_id': {'type': 'string'}, 'action': {'type': 'string', 'enum': ['enable', 'pause', 'delete', 'run', 'stop']},
                  'revision': {'type': 'integer'}, 'run_id': {'type': 'string'}, 'request_id': {'type': 'string'}}, ['schedule_id', 'action'])]


def _principal(owner):
    username = owner.username
    host = username == 'host' and owner._is_host_mode()
    return {'username': username, 'host_mode': host, 'host_workspace_id': owner.workspace_id if host else None,
            'role': owner.user_role, 'is_api_user': owner.user_role == 'api'}


def _guard(owner, service, definition, principal):
    """Scheduling cannot grant an Agent capabilities it does not currently possess."""
    from modules.execution_plane import get_execution_backend
    if getattr(get_execution_backend(owner), 'kind', 'local') != 'local':
        raise ValueError('定时任务暂不支持远程执行后端')
    if owner.get_work_mode() == 'plan' or owner.get_permission_mode() == 'readonly':
        raise PermissionError('只读或计划模式只能查询计划')
    validated = service.adapter.validate(definition, principal)
    data, _ = service.adapter._scope(validated, definition['target']['workspace_id'])
    settings, _ = service.adapter._settings(definition, validated, data)
    ranks = {'readonly': 0, 'approval': 1, 'auto_approval': 2, 'unrestricted': 3}
    if ranks[settings['permission_mode']] > ranks[owner.get_permission_mode()]:
        raise PermissionError('计划权限高于当前对话，请用户在定时任务页面设置')
    if settings['execution_mode'] == 'direct' and owner.get_execution_mode() != 'direct':
        raise PermissionError('当前沙箱对话不能创建或启动完全访问计划，请用户在页面设置')
    current_network = owner.get_network_permission()
    if {'none': 0, 'restricted': 1, 'full': 2}[settings['network_permission']] > {'none': 0, 'restricted': 1, 'full': 2}.get(current_network, 1):
        raise PermissionError('计划网络权限高于当前对话，请用户在页面设置')
    return validated


def execute(owner, name, arguments):
    service, p = get_service(), _principal(owner)
    username = p['username']
    if name == 'query_schedules':
        if arguments.get('schedule_id'):
            plans = [service.serialize_plan(service.store.get(username, arguments['schedule_id']))]
        else:
            plans = service.list(username, arguments.get('workspace_id'), arguments.get('enabled'))
        for plan in plans:
            if arguments.get('include_runs'):
                plan['runs'] = [service.serialize_run(run, detail=True) for run in service.store.runs(username, plan['definition']['id'])]
        fields = arguments.get('fields')
        if fields:
            allowed = {'definition', 'next_at', 'next_dates', 'created_at', 'updated_at', 'last_run', 'active_run', 'runs'}
            if set(fields) - allowed:
                raise ValueError('无效投影字段')
            plans = [{key: plan[key] for key in fields if key in plan} for plan in plans]
        return {'success': True, 'plans': plans}
    if name == 'submit_schedule':
        from modules.execution_plane import get_execution_backend
        result = get_execution_backend(owner).files.read_file(arguments['file_path'])
        if not result.get('success'):
            raise ValueError(result.get('error') or '无法读取配置文件')
        value = parse_definition(result['content'])
        old = service.store.get(username, value['id'])['definition'] if value.get('id') and arguments.get('overwrite') else None
        definition = normalize_definition(value, owner.workspace_id, previous=old)
        _guard(owner, service, definition, p)
        return {'success': True, 'plan': service.submit(username, value, p, owner.workspace_id,
                 overwrite=arguments.get('overwrite', False), source={'kind': 'agent', 'conversation_id': owner.context_manager.current_conversation_id, 'file_path': arguments['file_path']})}
    plan = service.store.get(username, arguments['schedule_id'])
    if arguments['action'] in {'enable', 'run'}:
        _guard(owner, service, plan['definition'], plan['principal'])
    elif owner.get_work_mode() == 'plan' or owner.get_permission_mode() == 'readonly':
        raise PermissionError('只读或计划模式只能查询计划')
    return {'success': True, 'result': service.manage(username, arguments['schedule_id'], arguments['action'],
             **{key: arguments[key] for key in ('revision', 'run_id', 'request_id') if key in arguments})}
