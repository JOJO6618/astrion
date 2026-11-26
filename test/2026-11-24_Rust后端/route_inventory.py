"""Inventory actual Flask URL maps and compare Axum route declarations without a listener."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from difflib import unified_diff
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASELINE = HERE / 'route_inventory_baseline.tsv'
RUST_MODES = {'full': 'web', 'headless': 'headless'}
PROFILES = {
    'full': 'server.app:app',
    'headless': 'server.headless_app:create_headless_app()',
}
AUXILIARY_METHODS = {'HEAD', 'OPTIONS'}
DENIED_EVENTS = {
    'socket.bind', 'socket.connect', 'socket.sendto', 'socket.getaddrinfo',
    'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.getnameinfo',
    'subprocess.Popen', 'os.exec', 'os.posix_spawn', 'os.system',
}
RouteKey = tuple[str, str]


def flask_snapshot(profile: str) -> None:
    scratch = Path(os.environ['ASTRION_ROUTE_INVENTORY_ROOT']).resolve(strict=True)
    if scratch.parent != HERE or not scratch.name.startswith('route_inventory-run-'):
        raise RuntimeError('Flask collection requires the isolated worker environment')
    if Path.cwd() != scratch / 'workspace' or os.environ.get('ASTRION_IGNORE_DOTENV') != '1':
        raise RuntimeError('Flask collection requires an isolated workspace and disabled dotenv loading')

    blocked = []

    def guard(event: str, args: tuple) -> None:
        if event in DENIED_EVENTS:
            caller = sys._getframe(1)
            if (event == 'socket.bind' and args[1] == ('::1', 0)
                    and caller.f_globals.get('__name__') == 'urllib3.util.connection'
                    and caller.f_code.co_name == '_has_ipv6'):
                # urllib3 tolerates a failed import-time IPv6 probe. No route depends on it.
                raise OSError('Route inventory runs without sockets')
            source = f'{caller.f_code.co_filename}:{caller.f_lineno}'
            blocked.append(f'{event} at {source}')
            raise RuntimeError(f'Route inventory blocked {event}')
        if event == 'open' and isinstance(args[0], (str, bytes)):
            path = Path(os.fsdecode(args[0])).resolve()
            flags = args[2]
            write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            if (flags & write_flags and not path.is_relative_to(scratch)) or path.name == '.env':
                blocked.append(event)
                raise RuntimeError('Route inventory blocked an open outside the isolated configuration')

    sys.addaudithook(guard)
    sys.path.insert(0, str(ROOT))
    # Keep import logs on stderr. Stdout is the worker's JSON channel.
    with redirect_stdout(sys.stderr):
        if profile == 'full':
            from server.app import app
        else:
            from server.headless_app import create_headless_app

            app = create_headless_app()

        rules = []
        blueprint_rules = Counter()
        for rule in app.url_map.iter_rules():
            view = inspect.unwrap(app.view_functions[rule.endpoint])
            blueprint = rule.endpoint.rpartition('.')[0] or '(app)'
            blueprint_rules[blueprint] += 1
            rules.append({
                'path': rule.rule,
                'methods': sorted(rule.methods or ()),
                'endpoint': rule.endpoint,
                'module': view.__module__,
                'automatic_options': bool(rule.provide_automatic_options),
            })

    if blocked:
        raise RuntimeError(f'Flask assembly attempted prohibited operations: {sorted(set(blocked))}')
    print(json.dumps({
        'entry': PROFILES[profile],
        'blueprints': sorted(app.blueprints),
        'rules_by_blueprint': dict(sorted(blueprint_rules.items())),
        'rules': rules,
    }))


def run_flask(profile: str) -> dict:
    with TemporaryDirectory(prefix='route_inventory-run-', dir=HERE) as directory:
        scratch = Path(directory)
        for name in ('home', 'workspace', 'config', 'tmp'):
            (scratch / name).mkdir()
        # Prevent the model-profile loader from falling back to a checkout's local models file.
        (scratch / 'config/custom_models.json').write_text('{"models": []}\n', encoding='utf-8')
        env = {name: os.environ[name] for name in ('SystemRoot', 'WINDIR') if name in os.environ}
        env.update({
            'PATH': os.defpath,
            'HOME': str(scratch / 'home'),
            'USERPROFILE': str(scratch / 'home'),
            'TMPDIR': str(scratch / 'tmp'),
            'TMP': str(scratch / 'tmp'),
            'TEMP': str(scratch / 'tmp'),
            'ASTRION_ROUTE_INVENTORY_ROOT': directory,
            'ASTRION_IGNORE_DOTENV': '1',
            'ASTRION_DATA_ROOT': str(scratch / 'runtime'),
            'DEPLOY_CONFIG_DIR': str(scratch / 'config'),
            'DEFAULT_PROJECT_PATH': str(scratch / 'workspace'),
            'HOST_PROJECT_PATH': str(scratch / 'workspace'),
            'TERMINAL_SANDBOX_MODE': 'host',
            'WEB_SECRET_KEY': 'route-inventory-placeholder',
        })
        process = subprocess.run(
            [sys.executable, '-I', '-B', '-X', 'utf8', str(Path(__file__).resolve()), '--worker', profile],
            cwd=scratch / 'workspace', env=env, capture_output=True, encoding='utf-8', timeout=30,
        )
        if process.returncode:
            raise RuntimeError(f'{PROFILES[profile]} failed with {sys.executable}\n{process.stderr.strip()}')
        return json.loads(process.stdout)


FLASK_PARAMETER = re.compile(r'<(?:(?P<converter>[^<>]+):)?[A-Za-z_]\w*>')


def normalize_flask(path: str) -> str:
    def normalize(match: re.Match) -> str:
        converter = match['converter']
        if converter in (None, 'string'):
            return '{param}'
        if converter == 'path':
            return '{*param}'
        return '{' + converter + ':param}'

    return FLASK_PARAMETER.sub(normalize, path)


def python_routes(snapshot: dict) -> dict[RouteKey, list[str]]:
    routes = defaultdict(set)
    for rule in snapshot['rules']:
        path = normalize_flask(rule['path'])
        for method in rule['methods']:
            auto = ' [auto OPTIONS]' if method == 'OPTIONS' and rule['automatic_options'] else ''
            routes[method, path].add(f"{rule['path']} → {rule['endpoint']} [{rule['module']}]{auto}")
    return {key: sorted(labels) for key, labels in routes.items()}


def rust_status(declarations: list, mode: str) -> str:
    kinds = {route.kind for route in declarations if mode in route.modes}
    for kind in ('declared', 'via_get', 'wildcard'):
        if kind in kinds:
            return kind
    if any(not route.modes for route in declarations):
        return 'unconfirmed'
    return 'missing'


def summary(full: dict, headless: dict, rust: dict, *, api_only: bool = False) -> dict:
    def in_scope(key: RouteKey) -> bool:
        path = key[1]
        return not api_only or path == '/api' or path.startswith('/api/')

    python = {profile: {key for key in routes if in_scope(key)}
              for profile, routes in zip(PROFILES, (full, headless))}
    source = {key: declarations for key, declarations in rust.items() if in_scope(key)}
    status = {mode: {key: rust_status(items, mode) for key, items in source.items()}
              for mode in RUST_MODES.values()}
    explicit = {mode: {key for key, value in values.items() if value == 'declared'}
                for mode, values in status.items()}
    mounted = {mode: {key for key, value in values.items() if value in {'declared', 'via_get'}}
               for mode, values in status.items()}
    source_explicit = {key for key, items in source.items() if any(item.kind == 'declared' for item in items)}
    all_keys = python['full'] | python['headless'] | set(source)
    result = {
        'method_columns': ['full', 'headless', 'rust_web_declared', 'rust_headless_declared',
                           'full_rust_web_match', 'headless_rust_headless_match'],
        'by_method': {},
        'rust_source_explicit': len(source_explicit),
        'rust_source_unconfirmed_explicit': len(source_explicit - explicit['web'] - explicit['headless']),
        'rust_modes': {},
    }
    for method in sorted({method for method, _ in all_keys}):
        method_keys = {key for key in all_keys if key[0] == method}
        result['by_method'][method] = [
            len(method_keys & routes) for routes in (
                *python.values(), explicit['web'], explicit['headless'],
                python['full'] & mounted['web'], python['headless'] & mounted['headless'],
            )
        ]
    for profile, mode in RUST_MODES.items():
        primary = {key for key in python[profile] if key[0] not in AUXILIARY_METHODS}
        result[profile] = {
            'effective': len(python[profile]),
            'primary': len(primary),
            'primary_rust_declared': len(primary & explicit[mode]),
            'primary_missing': len(primary - explicit[mode]),
        }
        result['rust_modes'][mode] = {
            'declared': len(explicit[mode]),
            'effective': len(mounted[mode]),
            'wildcards': list(status[mode].values()).count('wildcard'),
        }
    return result


def render_inventory() -> tuple[str, dict, dict]:
    from route_inventory_rust import rust_routes

    snapshots = {profile: run_flask(profile) for profile in PROFILES}
    full, headless = (python_routes(snapshots[profile]) for profile in PROFILES)
    rust = rust_routes(ROOT)
    totals = summary(full, headless, rust)
    api = summary(full, headless, rust, api_only=True)
    lines = ['# route_inventory schema=2']
    for profile, snapshot in snapshots.items():
        metadata = {key: value for key, value in snapshot.items() if key != 'rules'}
        metadata['flask_rules'] = len(snapshot['rules'])
        lines.append(f'# {profile} {json.dumps(metadata, ensure_ascii=False, sort_keys=True)}')
    lines.append('# rust_source backend-rust/src/**/*.rs (declarations and known mounting scopes)')
    lines.append(f'# totals {json.dumps(totals, sort_keys=True)}')
    lines.append(f'# api_totals {json.dumps(api, sort_keys=True)}')
    lines.append('method\tpath\tflask_full\tflask_headless\trust_web\trust_headless\trust_source')
    for method, path in sorted(set(full) | set(headless) | set(rust), key=lambda key: (key[1], key[0])):
        key = (method, path)
        declarations = rust.get(key, [])
        labels = [f"{route.label} [{','.join(route.modes) or 'unconfirmed'}; {route.kind}]"
                  for route in declarations]
        lines.append('\t'.join((method, path, '; '.join(full.get(key, ('-',))),
                               '; '.join(headless.get(key, ('-',))), rust_status(declarations, 'web'),
                               rust_status(declarations, 'headless'), '; '.join(labels) or '-')))
    return '\n'.join(lines) + '\n', totals, api


def print_summary(totals: dict, api: dict) -> None:
    for title, counts in (('All routes', totals), ('/api routes', api)):
        print(title)
        for profile in PROFILES:
            item = counts[profile]
            print(f"  {profile}: {item['effective']} effective method/path pairs, {item['primary']} primary, "
                  f"{item['primary_rust_declared']} declared in Rust, {item['primary_missing']} missing")
        for mode, rust in counts['rust_modes'].items():
            print(f"  Rust {mode}: {rust['declared']} mounted declarations, {rust['effective']} with HEAD via GET, "
                  f"{rust['wildcards']} ANY routes")
        print(f"  Rust source: {counts['rust_source_explicit']} explicit, "
              f"{counts['rust_source_unconfirmed_explicit']} with no confirmed mount")
        by_method = ', '.join(f'{method} {row[0]}/{row[1]}/{row[2]}/{row[3]}' for method, row in counts['by_method'].items())
        print(f'  By method (Flask full/Flask headless/Rust Web/Rust headless declarations): {by_method}')
    print('Route declaration and mounting scope are inventoried. Handler behavior is verified separately.')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='check the committed baseline (default)')
    mode.add_argument('--write-baseline', action='store_true', help='replace the baseline after reviewing route changes')
    mode.add_argument('--print', action='store_true', help='emit the current complete TSV')
    mode.add_argument('--worker', choices=PROFILES, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        flask_snapshot(args.worker)
        return 0

    try:
        inventory, totals, api = render_inventory()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(error, file=sys.stderr)
        return 2
    if args.print:
        print(inventory, end='')
        return 0

    print_summary(totals, api)
    if args.write_baseline:
        BASELINE.write_text(inventory, encoding='utf-8', newline='\n')
        print(f'Wrote {BASELINE.name}')
        return 0
    if not BASELINE.is_file():
        print(f'{BASELINE.name} is missing. Use --write-baseline to create it.', file=sys.stderr)
        return 1
    previous = BASELINE.read_text(encoding='utf-8')
    if previous != inventory:
        diff = unified_diff(previous.splitlines(keepends=True), inventory.splitlines(keepends=True),
                            fromfile=BASELINE.name, tofile='current route inventory')
        print(''.join(diff), end='', file=sys.stderr)
        return 1
    print(f'{BASELINE.name} matches the current route inventory')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
