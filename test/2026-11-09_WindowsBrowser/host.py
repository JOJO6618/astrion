"""Run isolated native probes with the installed Windows toolchain."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
TEST = Path(__file__).resolve().parent
ARTIFACTS = ROOT / '_experiments/windows-browser-20261109'
CARGO = Path.home() / '.cargo/bin/cargo.exe'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
env = os.environ.copy()
env['CARGO_TARGET_DIR'] = str(ROOT / 'desktop/src-tauri/target')
env['PATH'] = str(CARGO.parent) + os.pathsep + env.get('PATH', '')

def run(command, log, timeout=900):
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run([str(value) for value in command], cwd=ROOT, env=env,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
    print(log.read_text(encoding='utf-8', errors='replace')[-(14000 if result.returncode else 2000):])
    print('EXIT', result.returncode, 'LOG', log)
    return result.returncode

if sys.argv[1] == 'build-probe':
    lock = TEST / 'native-probe/Cargo.lock'
    if not lock.exists():
        shutil.copy2(ROOT / 'desktop/src-tauri/Cargo.lock', lock)
    sys.exit(run([CARGO, 'build', '--offline', '--manifest-path', TEST / 'native-probe/Cargo.toml'],
                 ARTIFACTS / 'probe-build.log'))
elif sys.argv[1] == 'run-probe':
    env['ASTRION_BROWSER_PROBE_ROOT'] = str(ARTIFACTS / 'probe-profile')
    executable = ROOT / 'desktop/src-tauri/target/debug/astrion-browser-probe.exe'
    for phase in ('write', 'read'):
        code = run([executable, '--' + phase], ARTIFACTS / ('probe-' + phase + '.log'), 60)
        if code:
            sys.exit(code)
        report = ARTIFACTS / 'probe-profile' / (phase + '.json')
        data = json.loads(report.read_text(encoding='utf-8'))
        print(json.dumps(data, ensure_ascii=False, indent=2))
        assert data['popup:adopted'] is True
        assert data['popup:popup']['value']['hasOpener'] is True
        assert data['page-a:opener-roundtrip']['value']['ok'] is True
        assert 'astrion_probe=synthetic' in data['page-a:loaded']['value']['cookie']
        assert data['page-a:loaded']['value']['stored'] == 'synthetic'
        assert data['page-b:loaded']['value']['cookie'] == ''
        assert data['page-b:loaded']['value']['stored'] is None
        assert data['main:loaded']['value']['cookie'] == ''
        assert 'main:iframe' not in data, 'iframe messages reached the main top-level listener'
        assert all(data['page-a:interfaces'].values())
        print('PASS native popup, opener roundtrip, profile isolation, persistent cookie/storage, top-frame IPC, runtime APIs:', phase)
elif sys.argv[1] == 'build-production-probe':
    sys.exit(run([CARGO, 'build', '--offline', '--manifest-path', TEST / 'production-probe/Cargo.toml'], ARTIFACTS / 'production-probe-build.log', 1800))
elif sys.argv[1] == 'test-ai-native':
    probe_root = ARTIFACTS / ('ai-run-' + str(time.time_ns()))
    env['ASTRION_BROWSER_PRODUCTION_PROBE_ROOT'] = str(probe_root)
    env['ASTRION_BROWSER_PROBE_PYTHON'] = sys.executable
    executable = ROOT / 'desktop/src-tauri/target/debug/astrion-browser-production-probe.exe'
    code = run([executable, '--ai'], ARTIFACTS / 'ai-native.log', 500)
    if code:
        sys.exit(code)
    data = json.loads((probe_root / 'ai.json').read_text(encoding='utf-8'))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    assert data['ok'], data
    (ARTIFACTS / 'ai-latest.json').write_text(json.dumps({'root': str(probe_root)}, indent=2), encoding='utf-8')
elif sys.argv[1] == 'test-ai-startup':
    sys.exit(run([sys.executable, '-B', TEST / 'test_ai_startup.py'], ARTIFACTS / 'ai-startup.log', 60))
elif sys.argv[1] == 'test-ai-backend':
    env['ASTRION_IGNORE_DOTENV'] = '1'
    env['ASTRION_DATA_ROOT'] = str(ARTIFACTS / ('ai-backend-' + str(time.time_ns())))
    sys.exit(run([sys.executable, '-B', ROOT / 'test/2026-11-09_AI浏览器工具/backend_regression.py'], ARTIFACTS / 'ai-backend.log', 120))
elif sys.argv[1] == 'run-production-probe':
    probe_root = ARTIFACTS / ('production-run-' + str(time.time_ns()))
    env['ASTRION_BROWSER_PRODUCTION_PROBE_ROOT'] = str(probe_root)
    env['ASTRION_BROWSER_PROBE_PYTHON'] = sys.executable
    from import_fixture import create
    env['LOCALAPPDATA'] = str(create(probe_root))
    executable = ROOT / 'desktop/src-tauri/target/debug/astrion-browser-production-probe.exe'
    for phase in ('write', 'read'):
        code = run([executable, '--' + phase], ARTIFACTS / ('production-' + phase + '.log'), 100)
        if code:
            sys.exit(code)
        data = json.loads((probe_root / (phase + '.json')).read_text(encoding='utf-8'))
        print(json.dumps(data, ensure_ascii=False, indent=2))
        assert data['suite'] and data['suite']['ok'] is True, data['suite']
        assert data['pixels']['green'] > 10000 and data['pixels']['blue'] > 10000
        assert data['pixels']['holeShowsMain'] and data['pixels']['outsideShowsPage'], data['pixels']
        print('PASS verbatim production browser module:', phase)
    (ARTIFACTS / 'production-latest.json').write_text(json.dumps({'root': str(probe_root)}, indent=2), encoding='utf-8')
elif sys.argv[1] == 'test-import':
    sys.exit(run([sys.executable, '-B', TEST / 'test_chrome_import.py', '-v'], ARTIFACTS / 'import-tests.log', 120))
elif sys.argv[1] == 'test-ui':
    sys.exit(run(['node', TEST / 'test_ui.cjs'], ARTIFACTS / 'ui-tests.log', 120))
elif sys.argv[1] in ('format-ui', 'lint-ui'):
    # Enumerate the directory instead of a hand-kept list: a removed or added component used to
    # leave a stale entry here (BrowserPageDialog.vue was deleted when dialogs became native UI).
    browser = ROOT / 'static/src/components/browser'
    files = sorted(path for path in browser.iterdir() if path.suffix in ('.vue', '.ts'))
    files += [ROOT / 'static/src/stores/nativeBrowser.ts', ROOT / 'static/src/locales/zh-CN/browser.ts', ROOT / 'static/src/locales/en-US/browser.ts']
    if sys.argv[1] == 'format-ui':
        command = ['node', ROOT / 'node_modules/prettier/bin/prettier.cjs', '--write', *files]
    else:
        command = ['node', ROOT / 'node_modules/eslint/bin/eslint.js', *files, '--max-warnings=0']
    sys.exit(run(command, ARTIFACTS / (sys.argv[1] + '.log'), 180))
elif sys.argv[1] == 'frontend':
    sys.exit(run(['cmd.exe', '/d', '/c', 'npm run build --silent'], ARTIFACTS / 'frontend-build.log', 1800))
elif sys.argv[1] in ('check-native', 'test-native', 'format-native', 'build-native'):
    action = sys.argv[1]
    manifest = ROOT / 'desktop/src-tauri/Cargo.toml'
    if action == 'format-native':
        files = sorted((ROOT / 'desktop/src-tauri/src/browser').glob('*.rs'))
        sys.exit(run([CARGO.parent / 'rustfmt.exe', '--edition', '2021', *files], ARTIFACTS / 'native-format.log'))
    args = {'check-native': ['check'], 'test-native': ['test'], 'build-native': ['build']}[action]
    args += ['--offline', '--manifest-path', manifest]
    if action == 'test-native':
        args += ['browser', '--', '--test-threads=1']
    sys.exit(run([CARGO, *args], ARTIFACTS / (action + '.log'), 1800))
else:
    raise SystemExit('unknown host operation')
