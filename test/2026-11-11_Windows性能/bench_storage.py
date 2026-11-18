"""Benchmark isolated JSON copies; source desktop data stays read-only."""
import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ['ASTRION_IGNORE_DOTENV'] = '1'
os.environ['TERMINAL_SANDBOX_MODE'] = 'host'


def timed(label, fn):
    start = time.perf_counter()
    value = fn()
    print(json.dumps({'step':label,'seconds':round(time.perf_counter()-start, 4)}, ensure_ascii=False), flush=True)
    return value


def main():
    root = Path.home() / '.astrion' / 'astrion-desktop'
    runtime = Path(os.environ.get('LOCALAPPDATA', '')) / 'Astrion' / 'runtime'
    print('runtime_entries', [p.name for p in runtime.iterdir()] if runtime.exists() else [])
    logs = root / 'host' / 'logs' / 'perf_debug.log'
    if logs.exists():
        # Existing perf labels contain IDs and timings only in these selected rows.
        rows = [line for line in logs.read_text(encoding='utf-8',errors='replace').splitlines()
                if 'elapsed_ms=' in line and ('create_new_conversation done' in line or '_ensure_conversation_versioning_enabled done' in line)]
        print('recent_creation_timings', rows[-15:])
    paths = sorted((root/'host'/'data'/'conversations').glob('**/conv_*.json'), key=lambda p:p.stat().st_size, reverse=True)
    source = paths[0]
    payload = timed('read_json_22MB',lambda:json.loads(source.read_text(encoding='utf-8')))
    print('shape', {'bytes':source.stat().st_size,'messages':len(payload.get('messages',[])), 'metadata_keys':list(payload.get('metadata',{}))})
    with tempfile.TemporaryDirectory(prefix='astrion-perf-') as tmp:
        os.environ['ASTRION_DATA_ROOT'] = tmp
        from utils.conversation_manager import ConversationManager
        cm = timed('manager_init', lambda:ConversationManager(base_dir=tmp))
        dst=Path(tmp)/'test.json'
        timed('atomic_json_save',lambda:cm._atomic_write_json(dst,payload))
        from copy import deepcopy
        timed('deepcopy',lambda:deepcopy(payload))
        timed('json_dumps',lambda:json.dumps(payload,ensure_ascii=False,indent=2))
        conv_id=payload['id']
        cm._atomic_write_json(cm._get_conversation_file_path(conv_id), payload)
        pr=cProfile.Profile()
        pr.enable()
        timed('save_conversation',lambda:cm.save_conversation(conv_id,payload['messages']))
        pr.disable()
        out=io.StringIO()
        pstats.Stats(pr,stream=out).strip_dirs().sort_stats('cumulative').print_stats(28)
        print(out.getvalue())


if __name__=='__main__':
    main()
