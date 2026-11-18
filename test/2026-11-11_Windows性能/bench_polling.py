"""Measure real task-list serialization on an isolated 22 MB conversation copy."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import argparse
import contextlib
import inspect
import io
import json
import os
import socket
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--label', default='baseline')
    parser.add_argument('--calls', type=int, default=6)
    parser.add_argument('--rows', type=int, default=4)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo))
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(OSError('Offline benchmark'))
    with tempfile.TemporaryDirectory(prefix='polling-bench-') as directory:
        root = Path(directory)
        data = root / 'data'
        os.environ.update(ASTRION_IGNORE_DOTENV='1', ASTRION_DATA_ROOT=str(root), DATA_DIR=str(data),
                          LOGS_DIR=str(root / 'logs'), DEPLOY_CONFIG_DIR=str(root / 'config'),
                          USER_SPACE_DIR=str(root / 'users'), API_USER_SPACE_DIR=str(root / 'api'),
                          TERMINAL_SANDBOX_MODE='host', WEB_API_SILENT='1')
        folder = data / 'conversations' / 'benchmark'
        folder.mkdir(parents=True)
        source_root = Path.home() / '.astrion/astrion-desktop/host/data/conversations'
        source = max(source_root.glob('**/conv_*.json'), key=lambda p: p.stat().st_size)
        raw = source.read_bytes()
        (folder / 'conv_probe.json').write_bytes(raw)
        principal = SimpleNamespace(conversation_kind='agent')
        records = [SimpleNamespace(task_id=f't{i}', username='host', workspace_id='benchmark',
                   conversation_id='conv_probe', principal=principal, status='running', created_at=i,
                   updated_at=i, message='probe', error=None, goal_progress=None, task_type='chat',
                   task_params=SimpleNamespace(message_source='user', goal_mode=False)) for i in range(args.rows)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from server.runtime.service import RuntimeService
            service = RuntimeService()
            supports_light = 'include_metadata' in inspect.signature(service.list_runs).parameters
            from modules import host_workspace_manager
            with patch('server.tasks.task_manager.list_tasks', return_value=records), \
                 patch.object(host_workspace_manager, 'load_host_workspace_catalog', return_value={
                     'workspaces': [{'workspace_id': 'benchmark', 'label': 'synthetic workspace'}]}):
                result = {'bytes': len(raw), 'rows': args.rows, 'calls': args.calls}
                for mode in ('public_list', 'tab_status_only'):
                    kw = {'include_metadata': False} if mode == 'tab_status_only' and supports_light else {}
                    start = time.perf_counter()
                    for _ in range(args.calls):
                        rows = service.list_runs('host', 'benchmark', status='active', **kw)
                        assert len(rows) == args.rows
                    result[mode] = round(time.perf_counter() - start, 6)
        out = Path(__file__).resolve().parent / f'polling_{args.label}.json'
        out.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
