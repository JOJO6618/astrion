"""Benchmark shallow finalization on copied backups; live files are read-only."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    args=parser.parse_args()
    sys.path.insert(0,str(args.repo))
    desktop=Path.home()/'.astrion'/'astrion-desktop'/'host'/'data'
    conv=max((desktop/'conversations').glob('**/conv_*.json'),key=lambda p:p.stat().st_size)
    payload=json.loads(conv.read_text(encoding='utf-8'))
    conv_id=payload['id']
    project=payload['metadata']['project_path']
    results=[]
    def timed(label,fn):
        start=time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            val=fn()
        row={'stage':label,'seconds':round(time.perf_counter()-start,4)}
        results.append(row)
        print(row,flush=True)
        return val
    with tempfile.TemporaryDirectory(prefix='astrion-finalize-') as tmp:
        os.environ.update({'ASTRION_IGNORE_DOTENV':'1','TERMINAL_SANDBOX_MODE':'host',
                           'ASTRION_DATA_ROOT':tmp,'DATA_DIR':tmp,'LOGS_DIR':str(Path(tmp)/'logs'),
                           'DEPLOY_CONFIG_DIR':str(Path(tmp)/'config')})
        original=desktop/'save'/conv_id
        copy=Path(tmp)/'save'/conv_id
        timed('copy_backups',lambda:shutil.copytree(original,copy,ignore=shutil.ignore_patterns('snapshots','git')))
        from modules.shallow_versioning import ShallowVersioningManager
        manager=timed('manager_init',lambda:ShallowVersioningManager(project,tmp,conv_id))
        print({'tracked':len(manager._tracked_files),'snapshots':len(manager._snapshots)},flush=True)
        target=manager._snapshots[-1].message_id
        timed('file_diff_stats',lambda:manager.get_file_diff_stats(target))
        timed('aggregate_diff_stats',lambda:manager.get_diff_stats(target))
        timed('make_snapshot',lambda:manager.make_snapshot('perf-isolated-input'))
        from modules.modify_history import _build_files_payload,render_modify_history_diff
        summary=max(((m.get('metadata') or {}).get('edit_summary',{}) for m in payload.get('messages',[])),key=lambda s:len(s.get('files',[])))
        timed('modify_history_render',lambda:render_modify_history_diff(conversation_id=conv_id,user_message_text='probe',started_at=None,finished_at=None,files=_build_files_payload(summary.get('files',[]))))
    out=args.repo/'test'/'2026-11-11_Windows性能'/'finalize.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(results,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
