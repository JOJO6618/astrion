"""Extract timing-only evidence and compare bundled/source code hashes."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

repo = Path(__file__).resolve().parents[2]
runtime = Path(os.environ['LOCALAPPDATA'])/'Astrion'/'runtime'/'backend'
files = ['core/web_terminal.py','server/chat_flow_task_main.py','utils/conversation_manager/crud_mixin.py',
         'utils/conversation_manager/index_mixin.py','modules/shallow_versioning.py',
         'core/main_terminal_parts/context/mode.py','server/status/git.py']
for name in files:
    source, bundled = repo/name, runtime/name
    print('code_match',name, bundled.exists() and hashlib.sha256(source.read_bytes()).digest()==hashlib.sha256(bundled.read_bytes()).digest())
logs=Path.home()/'.astrion'/'astrion-desktop'/'host'/'logs'
for name in ['perf_debug.log','conn_diag.log','host_workspace_debug.log']:
    path=logs/name
    if not path.exists():
        continue
    text=path.read_text(encoding='utf-8',errors='replace')
    metrics=[]
    for line in text.splitlines():
        found=re.findall(r'([A-Za-z_]*(?:ms|elapsed|duration|seconds)[A-Za-z_]*)[=:]\s*([0-9.]+)',line)
        for key,val in found:
            try:
                value=float(val)
            except ValueError:
                continue
            if value>=500:
                # Whitelist label characters, strip structured data and messages.
                label=line.split('|')[1].strip() if '|' in line else ''
                label=re.sub(r'[^A-Za-z0-9_ .\[\]-]','',label)[:95]
                metrics.append({'date':line[:26],'label':label,'metric':key,'value':value})
    print('slow_metrics',name,len(metrics),sorted(metrics,key=lambda row:row['value'],reverse=True)[:25])
    if name=='host_workspace_debug.log':
        events=Counter()
        for line in text.splitlines():
            try:
                row=json.loads(line)
                events[str(row.get('event',''))]+=1
            except ValueError:
                pass
        print('workspace_event_counts',events.most_common(15))
# Report only numeric/policy settings relevant to latency.
prefs=logs.parent/'data'/'personalization.json'
if prefs.exists():
    data=json.loads(prefs.read_text(encoding='utf-8'))
    print('performance_preferences',{k:data.get(k) for k in ['versioning_enabled_by_default','versioning_backup_mode','auto_generate_title','auto_shallow_compress_enabled','auto_deep_compress_enabled','modify_history_enabled']})
