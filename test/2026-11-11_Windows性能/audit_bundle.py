"""Read installed public code and backup metadata; output no conversation text."""
import difflib
import json
import os
from pathlib import Path

repo=Path(__file__).resolve().parents[2]
bundle=Path(os.environ['LOCALAPPDATA'])/'Astrion'/'runtime'/'backend'
output=repo/'_experiments'/'windows-latency-evidence'
output.mkdir(parents=True,exist_ok=True)
for name in ['server/chat_flow_task_main.py','utils/conversation_manager/crud_mixin.py',
             'modules/shallow_versioning.py','core/main_terminal_parts/context/mode.py']:
    old=(bundle/name).read_text(encoding='utf-8')
    new=(repo/name).read_text(encoding='utf-8')
    diff=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='installed/'+name,tofile='source/'+name))
    print('code_diff',name,len(diff))
    if diff:
        (output/(Path(name).stem+'.diff')).write_text(diff,encoding='utf-8')
data=Path.home()/'.astrion'/'astrion-desktop'/'host'/'data'
convs=sorted((data/'conversations').glob('**/conv_*.json'),key=lambda p:p.stat().st_size,reverse=True)
for conv in convs[:5]:
    payload=json.loads(conv.read_text(encoding='utf-8'))
    conv_id=payload['id']
    save=data/'save'/conv_id
    states=list(save.glob('**/*.jsonl')) if save.exists() else []
    results=[]
    for state in states:
        if 'inputs' in state.name:
            continue
        try:
            rows=[json.loads(s) for s in state.read_text(encoding='utf-8').splitlines() if s.strip()]
            keys=set()
            for row in rows:
                keys.update((row.get('tracked_file_backups') or {}).keys())
            results.append({'file':state.name,'rows':len(rows),'tracked_paths':len(keys),'bytes':state.stat().st_size})
        except ValueError:
            pass
    summaries=[(m.get('metadata') or {}).get('edit_summary',{}) for m in payload.get('messages',[])]
    sizes=[sum(len(str(f.get('baseline','')))+len(str(f.get('current_text',''))) for f in s.get('files',[]) if isinstance(f,dict)) for s in summaries if isinstance(s,dict)]
    print('backup_shape',{'id':conv_id,'bytes':conv.stat().st_size,'versioning':payload.get('metadata',{}).get('versioning',{}),'states':results,'max_edit_summary_chars':max(sizes,default=0)})
