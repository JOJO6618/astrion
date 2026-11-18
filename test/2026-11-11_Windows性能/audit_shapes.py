import json
from pathlib import Path

root=Path.home()/'.astrion'/'astrion-desktop'/'host'/'data'
for name in ['providers.json','codex_models.json','sub_agent_runtime.json']:
    p=root/name
    if p.exists():
        d=json.loads(p.read_text(encoding='utf-8'))
        print(name,{'bytes':p.stat().st_size,'keys':list(d),'providers':len(d.get('providers',{})),
                    'models':len(d.get('models',[])),'tasks':len(d.get('tasks',{}))})
        if name=='providers.json':
            print('provider_model_counts',[len(r.get('models',[])) for r in d.get('providers',{}).values()])
for p in (root/'codex_models').glob('*.json'):
    d=json.loads(p.read_text(encoding='utf-8'))
    print('codex_models_file',{'bytes':p.stat().st_size,'keys':list(d),'models':len(d.get('models',[]))})
p=max((root/'conversations').glob('**/conv_*.json'),key=lambda p:p.stat().st_size)
d=json.loads(p.read_text(encoding='utf-8'))
active=[m for m in d.get('messages',[]) if not (m.get('metadata') or {}).get('deep_compacted')]
print('active_history',{'messages':len(active),'reasoning_blocks':sum(bool((m.get('metadata') or {}).get('responses_reasoning_items')) for m in active)})
