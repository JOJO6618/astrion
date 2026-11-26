"""Collect Rust route declarations and check the current production assembly anchors."""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
import json
from pathlib import Path
import re
from typing import NamedTuple


METHODS = 'get head post put patch delete options trace connect any'.split()
PARAMETER = re.compile(r'\{(?P<wildcard>\*)?[A-Za-z_]\w*\}')
FUNCTION = re.compile(r'\bfn\s+(\w+)\s*(?:<[^()]*>\s*)?\(')
ROUTE = re.compile(r'\.\s*route\s*(\()')
ATTRIBUTE = re.compile(r'#\s*\[\s*(?:\w+\s*::\s*)*(?P<method>get|head|post|put|patch|delete|options|trace|connect|route)\s*(?P<open>\()')
STRING = r'"(?:[^"\\]|\\.)*"'
CONST_PATH = re.compile(rf'\bconst\s+([A-Z][A-Z_0-9]*)\s*:\s*&\s*(?:\'static\s+)?str\s*=\s*({STRING})')
NON_CODE = re.compile(rf'r(?P<hash>#*)"[\s\S]*?"(?P=hash)|/\*[\s\S]*?\*/|//[^\n]*|{STRING}|\'(?:[^\'\\\n]|\\.)\'')
DELIMITERS = {'(': ')', '[': ']', '{': '}'}
UNRESOLVED_NESTING = re.compile(r'\.\s*(?:nest|nest_service|route_service)\s*\(')
METHOD_CALL = re.compile(rf'(?:\w+::)*({"|".join(METHODS)})\s*(\()')


class RustRoute(NamedTuple):
    modes: tuple[str, ...]
    kind: str
    label: str


def code_only(text: str) -> str:
    return NON_CODE.sub(lambda match: ' ' * len(match[0]), text)


def split_call(text: str, code: str, opening: int) -> tuple[list[str], int]:
    """Find the comma-separated arguments of a call, preserving original strings."""
    closing = DELIMITERS[code[opening]]
    stack = [closing]
    start = opening + 1
    args = []
    for index in range(start, len(code)):
        char = code[index]
        if char in DELIMITERS:
            stack.append(DELIMITERS[char])
        elif char == stack[-1]:
            stack.pop()
            if not stack:
                last = text[start:index].strip()
                if last:
                    args.append(last)
                return args, index + 1
        elif char == ',' and len(stack) == 1:
            args.append(text[start:index].strip())
            start = index + 1
    raise RuntimeError('Unclosed Rust route or handler call')


def path_value(value: str, constants: dict[str, str]) -> str:
    if re.fullmatch(STRING, value):
        path = json.loads(value)
    else:
        path = constants.get(value, '')
    if not path.startswith('/'):
        raise RuntimeError(f'Cannot determine literal Rust route path from {value[:100]}')
    return path


def method_chain(expression: str, source: str = "") -> dict[str, str]:
    expression = expression.strip()
    code = code_only(expression)
    methods = {}
    index = 0
    if on := re.match(r'on\s*(\()', code):
        args, index = split_call(expression, code, on.start(1))
        flags = args[0]
        if re.fullmatch(r'[A-Z_]+', flags):
            decl = re.search(rf'const\s+{flags}\s*:\s*MethodFilter\s*=\s*([^;]+);', source)
            if decl is None:
                raise RuntimeError(f'Unknown route method filter {flags}')
            flags = decl[1]
        selected = re.findall(r'MethodFilter::([A-Z]+)', flags)
        if not selected:
            raise RuntimeError(f'Empty route method filter {flags}')
        methods.update(dict.fromkeys(selected, args[1].strip()))
        if chain := re.match(r'\s*\.\s*', code[index:]):
            index += chain.end()
    while match := METHOD_CALL.match(code, index):
        args, index = split_call(expression, code, match.start(2))
        method = match[1].upper()
        handler = args[0]
        methods[method] = handler if re.fullmatch(r'(?:\w+::)*\w+', handler) else '<closure>'
        chained = re.match(r'\s*\.\s*', code[index:])
        if not chained:
            break
        index += chained.end()
    return methods


def function_body(code: str, name: str) -> str:
    function = re.search(rf'\bfn\s+{name}\s*\(', code)
    if function is None:
        return ''
    opening = code.index('{', function.end())
    _, end = split_call(code, code, opening)
    return code[opening + 1:end - 1]


def known_mounts(sources: dict[str, str]) -> dict[tuple[str, str], tuple[str, ...]]:
    """Match the production router composition exercised by the two launch modes."""
    main = code_only(sources['main.rs'])
    host_code = code_only(sources.get('host_web.rs', ''))
    host = function_body(host_code, 'routes')
    gateway_router = code_only(sources.get('gateway.rs', ''))
    gateway = bool(re.search(r'\bgateway\s*::\s*router\s*\(', main))
    web = bool(re.search(r'\bhost_web\s*::\s*routes\s*\(', main))
    if not gateway or not web or not re.search(r'\bif\s+config\s*\.\s*web\b', main):
        raise RuntimeError('Rust launch mode or Gateway wiring changed. Review the route inventory mounting anchors.')
    mounts = {
        ('gateway.rs', 'router'): ('headless', 'web'),
        ('main.rs', 'serve'): ('headless',),  # Inline Router in the else arm of config.web.
        ('host_web.rs', 'routes'): ('web',),
    }
    if re.search(r'\bproviders\s*::\s*router\s*\(', main):
        modes = tuple(mode for mode, guard in [('web', 'auth'), ('headless', 'session_guard')]
                      if re.search(rf'\b{guard}\s*\.\s*protect_api\s*\(\s*providers\s*\)', main))
        mounts['providers.rs', 'router'] = modes
    for variable, constructor, source in (
        ('crosspet', 'CrossPet::start', 'crosspet.rs'),
        ('desktop_bridge', 'DesktopBridge::from_env', 'desktop_bridge/mod.rs'),
        ('project_storage', 'ProjectStorage::from_env', 'project_storage.rs'),
    ):
        if re.search(rf'\b{constructor}\s*\(', main):
            mounts[source, 'router'] = tuple(
                mode for mode, guard in [('web', 'auth'), ('headless', 'session_guard')]
                if re.search(rf'\b{guard}\s*\.\s*protect_api\s*\(\s*{variable}\s*\.\s*router\s*\(', main)
            )
    if (re.search(r'auth\.protect_api\(astrion_headless::providers::codex_router\(', main)
            and re.search(r'\bcodex_oauth::router\(', function_body(code_only(sources['providers.rs']), 'codex_router'))):
        mounts['providers/codex_oauth.rs', 'router'] = ('web',)
    if ('providers/codex_oauth.rs', 'router') in mounts and 'codex_lifecycle::router(' in code_only(sources['providers/codex_oauth.rs']):
        mounts['providers/codex_lifecycle/mod.rs', 'router'] = ('web',)
    if ('providers/codex_oauth.rs', 'router') in mounts and 'quota::router(' in function_body(code_only(sources['providers.rs']), 'codex_router'):
        quota = function_body(code_only(sources['providers/quota/mod.rs']), 'router')
        if 'assemble_router(' in quota:
            mounts['providers/quota/mod.rs','assemble_router'] = ('web',)
    gateway_anchors = {
        ('runtime/host_policy.rs','path_router'): r'\bhost_policy::path_router\s*\(',
        ('runtime/host_policy.rs','settings_router'): r'\bhost_policy::settings_router\s*\(',
        ('interactions/http.rs', 'router'): r'\binteractions::router\s*\(',
        ('runtime/process_resources.rs','gateway_router'): r'\bprocess_resources::gateway_router\s*\(',
        ('runtime/guidance.rs', 'router'): r'\bguidance::router\s*\(',
        ('versioning/mod.rs', 'host_token_router'): r'\bversioning\s*::\s*host_token_router\s*\(',
        ('goals/http.rs', 'router'): r'\bgoals\s*::\s*router(?:_with_notice_models)?\s*\(',
        ('workspace_routes.rs', 'router'): r'\bworkspace_routes\s*::\s*router\s*\(',
        ('sub_agents/routes.rs', 'list_router'): r'\bapp\s*\.\s*children\s*\.\s*list_router\s*\(',
        ('approval/http.rs', 'router'): r'\.\s*merge\s*\(\s*approval\s*::\s*router\s*\(',
        ('workflows/routes.rs', 'registry_list_router'): r'\.\s*merge\s*\(\s*workflows\s*::\s*registry_list_router\s*\(',
        ('web_assets/personalization/mod.rs', 'router'): r'\.\s*merge\s*\(\s*app\s*\.\s*preferences\s*\.\s*router\s*\(',
        ('schedules/routes.rs', 'router'): r'\.\s*merge\s*\(\s*schedules\s*::\s*router\s*\(',
    }
    web_anchors = {
        ('host_web/client_debug.rs','router'): r'\bclient_debug::router\s*\(',
        ('web_status/attachments.rs','attachment_router'): r'\bweb_status::attachment_router\s*\(',
        ('runtime/process_resources.rs','browser_router'): r'process_resources::browser_router\s*\(',
        ('runtime/process_resources.rs','monitor_router'): r'process_resources::monitor_router\s*\(',
        ('native_actions/routes.rs','workspace_router'): r'\.merge\(native\.workspace_router\(\)\)',
        ('native_actions/routes.rs','system_router'): r'\.merge\(native\.system_router\(\)\)',
        ('project_git/workspace.rs','workspace_router'): r'\bproject_git::workspace_router\s*\(',
        ('runtime/versioning.rs','mutation_router'): r'\bversioning::mutation_router\s*\(',
        ('runtime/transfer/http.rs','router'): r'\btransfer::router\s*\(',
        ('preview/routes.rs','router'): r'\.merge\(preview\.router\(\)\)',
        ('knowledge/migration/mod.rs','router'): r'\bknowledge::migration::router\s*\(',
        ('conversation_db/migration.rs','router'): r'\bconversation_db::migration::router\s*\(',
        ('mcp/routes.rs','router_with_registry'): r'\bmcp::router\s*\(',
        ('admin_routes.rs','router'): r'\bAdminRoutes::for_host\s*\(',
        ('web_status/chatgpt_fast_mode.rs', 'chatgpt_fast_mode_router'): r'\bweb_status::chatgpt_fast_mode_router\s*\(',
        ('runtime/conversation_duplicate.rs', 'browser_router'): r'\bconversation_duplicate::browser_router\s*\(',
        ('sub_agent_settings.rs', 'models_router'): r'\bsub_agent_settings::models_router\s*\(',
        ('sub_agent_settings.rs', 'settings_router'): r'\bsub_agent_settings::settings_router\s*\(',
        ('runtime/web_messages.rs', 'router'): r'\bweb_messages\s*::\s*router\s*\(',
        ('versioning/mod.rs', 'browser_router'): r'\bversioning\s*::\s*browser_router\s*\(',
        ('runtime/conversation_review/mod.rs', 'router'): r'\bconversation_review\s*::\s*router\s*\(',
        ('runtime/conversation_token_statistics.rs', 'router'): r'\bconversation_token_statistics\s*::\s*router\s*\(',
        ('chat_mode/routes.rs', 'router'): r'\bmerge\s*\(\s*chat\s*\.\s*router\s*\(',
        ('knowledge/web.rs', 'scoped_router'): r'\bknowledge\s*::\s*web\s*::\s*scoped_router\s*\(',
        ('worktree/routes.rs', 'router_with_scope'): r'\bworktree\s*::\s*router_with_scope\s*\(',
        ('sub_agents/routes.rs', 'browser_router'): r'\bgateway\s*\.\s*children\s*\.\s*browser_router\s*\(',
        ('web_assets.rs', 'router'): r'\bassets\s*\.\s*router\s*\(',
        ('web_auth/mod.rs', 'router_with_login'): r'\bauth\s*\.\s*router_with_login\s*\(\s*assets\s*\.\s*login\s*\(',
        ('navigation.rs', 'host_router_with_pending'): r'\.\s*merge\s*\(\s*navigation_routes\s*\)',
        ('project_git/mod.rs', 'router_with_scope'): r'\bProjectGit\s*::\s*router_with_scope\s*\(',
        ('storage_stats/mod.rs', 'router'): r'\bstorage_stats\s*::\s*router\s*\(',
        ('usage/mod.rs', 'routes'): r'\busage\s*::\s*(?:host_)?router\s*\(',
        ('web_conversations.rs', 'router'): r'\bweb_conversations\s*::\s*router\s*\(',
        ('web_status/mod.rs', 'routes'): r'\bweb_status\s*::\s*(?:scoped_)?router\s*\(',
        ('workflows/routes.rs', 'browser_routes'): r'\bworkflows\s*::\s*browser_(?:scoped_)?router\s*\(',
    }
    for source, anchors, modes in ((gateway_router, gateway_anchors, ('headless', 'web')), (host, web_anchors, ('web',))):
        for origin, pattern in anchors.items():
            if re.search(pattern, source):
                mounts[origin] = modes
    if ('web_conversations.rs', 'router') in mounts and 'lifecycle::router(' in sources['web_conversations.rs']:
        mounts['web_conversations/lifecycle.rs','router'] = ('web',)
    if ('runtime/host_policy.rs','settings_router') in mounts and 'host_policy_setup::router(' in function_body(code_only(sources['runtime/host_policy.rs']), 'settings_router'):
        mounts['runtime/host_policy_setup.rs','router'] = ('headless','web')
    browser = code_only(sources.get('knowledge/browser.rs', ''))
    if (re.search(r'\bBrowserPreferences::host_router\s*\(', host) and re.search(r'\.merge\(browser\)', host)
            and re.search(r'fn\s+host_router\b[^}]*?\.routes\(\)', browser)):
        mounts['knowledge/browser.rs', 'routes'] = ('web',)
    quota_modes = set()
    if ('usage/mod.rs', 'routes') in mounts and re.search(r'\.\s*merge\s*\(\s*quota_router\s*\(', code_only(sources['usage/mod.rs'])):
        quota_modes.add('web')
    if re.search(r'session_guard\s*\.\s*protect_api\s*\(\s*(?:astrion_headless::)?usage::quota_router\s*\(', main):
        quota_modes.add('headless')
    if quota_modes:
        mounts['usage/mod.rs', 'quota_router'] = tuple(sorted(quota_modes))
    status_code = code_only(sources['web_status/mod.rs'])
    status_routes = function_body(status_code, 'routes')
    status_modes = mounts.get(('web_status/mod.rs', 'routes'))
    if status_modes and re.search(r'\.\s*merge\s*\(\s*settings\s*::\s*router\s*\(', status_routes):
        mounts['web_status/settings.rs', 'router'] = status_modes
    if status_modes and re.search(r'\bstatus_route\s*\(', status_routes):
        mounts['web_status/mod.rs', 'status_route'] = status_modes
    knowledge_code = code_only(sources['knowledge/web.rs'])
    if (('knowledge/web.rs', 'scoped_router') in mounts
            and re.search(r'\.\s*merge\s*\(\s*skills_router\s*\(', function_body(knowledge_code, 'scoped_router'))):
        mounts['knowledge/web.rs', 'skills_router'] = ('web',)

    if re.search(r'\bsession_guard\s*\.\s*protect_api\s*\(\s*host_web\s*::\s*headless_workspace_routes\s*\(', main):
        headless = function_body(host_code, 'headless_workspace_routes')
        anchors = {
            ('project_git/workspace.rs','workspace_router'): r'project_git\s*::\s*workspace_router',
            ('knowledge/web.rs', 'skills_router'): r'knowledge\s*::\s*web\s*::\s*skills_router',
            ('worktree/routes.rs', 'router_with_scope'): r'worktree\s*::\s*router_with_scope',
        }
        selected = [origin for origin, callee in anchors.items()
                    if re.search(rf'\.\s*merge\s*\(\s*(?:astrion_headless\s*::\s*)?{callee}\s*\(', headless)]
        if (re.search(r'\bweb_status\s*::\s*headless_router\s*\(', headless)
                and re.search(r'\bbind_routes\s*\(\s*status_route\s*\(\s*\)', function_body(status_code, 'headless_router'))):
            selected.append(('web_status/mod.rs', 'status_route'))
        for symbol in ('workspace_router', 'system_router'):
            if f'.merge(native.{symbol}())' in headless:
                selected.append(('native_actions/routes.rs',symbol))
        for origin in selected:
            mounts[origin] = tuple(sorted({'headless', *mounts.get(origin, ())}))
    return mounts


def helper_methods(sources: dict[str, str], name: str) -> dict[str, str]:
    source = sources['web_assets.rs']
    code = code_only(source)
    function = re.search(rf'\bfn\s+{name}\s*\(', code)
    if function is None:
        raise RuntimeError(f'Cannot derive HTTP methods from WebAssets::{name}')
    opening = code.index('{', function.end())
    _, end = split_call(source, code, opening)
    methods = method_chain(source[opening + 1:end - 1])
    if not methods:
        raise RuntimeError(f'Cannot derive HTTP methods from WebAssets::{name}')
    return methods


def loop_paths(value: str, source: str, code: str, start: int, site: int) -> list[str] | None:
    """Expand a path bound to the first field of a tuple in an enclosing finite loop."""
    if not re.fullmatch(r'\w+', value):
        return None
    loop = re.compile(rf'\bfor\s*\(\s*{value}\s*,[^()]+\)\s+in\s*(\[|\w+)')
    for match in reversed(list(loop.finditer(code, start, site))):
        if match[1] == '[':
            opening = match.start(1)
            rows, after = split_call(source, code, opening)
        else:
            assignment = re.search(rf'\blet\s+{match[1]}\s*=\s*(\[)', code[start:match.start()])
            if assignment is None:
                continue
            rows, _ = split_call(source, code, start + assignment.start(1))
            after = match.end()
        body = re.compile(r'\s*(\{)').match(code, after)
        if body is None or body.end() > site:
            continue
        _, body_end = split_call(source, code, body.start(1))
        if body_end < site:
            continue
        paths = []
        for row in rows:
            entries, _ = split_call(row, code_only(row), 0)
            paths.append(path_value(entries[0], {}))
        return paths
    return None


def rust_routes(root: Path) -> dict[tuple[str, str], list[RustRoute]]:
    source_dir = root / 'backend-rust/src'
    sources = {path.relative_to(source_dir).as_posix(): path.read_text(encoding='utf-8')
               for path in sorted(source_dir.rglob('*.rs'))}
    mounts = known_mounts(sources)
    routes = defaultdict(set)

    for filename, source in sources.items():
        code = code_only(source)
        if UNRESOLVED_NESTING.search(code):
            raise RuntimeError(f'{filename} registers nested or service routes. Inventory their path prefix or method scope explicitly.')
        funcs = list(FUNCTION.finditer(code))
        offsets = [function.start() for function in funcs]
        constants = {name: json.loads(value) for name, value in CONST_PATH.findall(source)}
        for site in ROUTE.finditer(code):
            args, _ = split_call(source, code, site.start(1))
            if len(args) != 2:
                raise RuntimeError(f'{filename} contains an unsupported .route expression')
            # ApprovalSettings::route chooses a tool approval policy. It does not register HTTP.
            if filename == 'runtime/approval.rs' and args in (['&scope', '&request'], ['scope', 'request']):
                continue
            scope = bisect_left(offsets, site.start()) - 1
            function = funcs[scope][1] if scope >= 0 else '<module>'
            try:
                paths = (loop_paths(args[0], source, code, offsets[scope], site.start()) if scope >= 0 else None)
                if paths is None:
                    paths = [path_value(args[0], constants)]
            except (RuntimeError, KeyError, IndexError) as error:
                raise RuntimeError(f'{filename}::{function}: {error}') from error
            origin = (filename, function)
            methods = method_chain(args[1], source)
            if origin == ('web_assets.rs', 'router') and args[1].startswith('page('):
                methods = dict.fromkeys(helper_methods(sources, 'page'), args[1])
            elif origin == ('web_auth/mod.rs', 'router_with_login') and args[1] == 'login':
                methods = dict.fromkeys(helper_methods(sources, 'login'), 'WebAssets::login')
            if not methods:
                raise RuntimeError(f'{filename}::{function} cannot resolve HTTP methods for {paths}: {args[1][:100]}')
            for path in paths:
                register(routes, path, methods, mounts.get(origin, ()), f'{filename}::{function}')

        for site in ATTRIBUTE.finditer(code):
            args, _ = split_call(source, code, site.start('open'))
            path = path_value(args[0], constants)
            method = site['method'].upper()
            if method == 'ROUTE':
                method_arg = re.fullmatch(rf'method\s*=\s*({STRING})', args[1] if len(args) == 2 else '')
                if method_arg is None or json.loads(method_arg[1]).lower() not in METHODS:
                    raise RuntimeError(f'{filename} contains an unsupported route attribute for {path}')
                method = json.loads(method_arg[1]).upper()
            register(routes, path, {method: 'route attribute'}, (), filename)

    auto_options = 'from_fn(route_options)' in sources['main.rs']
    for (method, path), declarations in list(routes.items()):
        if auto_options and method not in ('ANY', 'OPTIONS'):
            for declaration in declarations:
                routes['OPTIONS', path].add(RustRoute(declaration.modes, 'via_options', declaration.label))
        if method == 'GET':
            for declaration in declarations:
                routes['HEAD', path].add(RustRoute(declaration.modes, 'via_get', declaration.label))
    return {key: sorted(declarations) for key, declarations in routes.items()}


def register(routes: dict, raw_path: str, methods: dict[str, str], modes: tuple[str, ...], origin: str) -> None:
    path = PARAMETER.sub(lambda match: '{*param}' if match['wildcard'] else '{param}', raw_path)
    # Axum's detail handler enforces Flask's unsigned integer converter itself.
    if raw_path == '/api/conversations/{id}/versioning/checkpoints/{seq}' and methods.get('GET') == 'detail':
        path = path.removesuffix('{param}') + '{int:param}'
    if origin == 'web_assets.rs::router' and methods.get('ANY') == 'conversation' and raw_path.endswith('/{conversation_id}'):
        # The handler validates the conversation converter before dispatching these methods.
        path = path.removesuffix('{param}') + '{conv:param}'
        methods = {method: 'conversation' for method in ('GET','HEAD','OPTIONS')}
    if origin == 'preview/routes.rs::router' and '/proxy/{port}/' in raw_path:
        path = path.replace('/proxy/{param}/','/proxy/{int:param}/')
    for method, handler in methods.items():
        kind = 'wildcard' if method == 'ANY' else 'declared'
        routes[method, path].add(RustRoute(modes, kind, f'{raw_path} → {handler} [{origin}]'))
