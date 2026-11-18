"""Print only timestamps and whitelisted stage categories from desktop logs."""
from pathlib import Path
import re

root=Path.home()/'.astrion'/'astrion-desktop'
print('desktop_log_files',[(p.name,p.stat().st_size) for p in (root/'logs').glob('*') if p.is_file()])
markers={
    'API_FIRST':'[API] 第1次调用',
    'STREAM_END':'流结束统计',
    'API_DONE':'[API] 请求完成',
    'TASK_START':'handle_task_with_sender_start',
    'WORK_TIMER':'work_timer',
    'SNAPSHOT_BEGIN':'[ShallowVersioning] make_snapshot called',
    'SNAPSHOT_DONE':'[ShallowVersioning] make_snapshot done',
    'CHECKPOINT_DONE':'[Versioning][ShallowCheckpoint]',
    'CONTEXT_BUILD':'main_terminal.build_messages.context_snapshot',
    'TASK_BIND':'tasks.run_chat_task.apply_host_session',
}
for name in ['debug_stream.log','perf_debug.log','host_workspace_debug.log']:
    path=root/'host'/'logs'/name
    if not path.exists():
        continue
    entries=[]
    for line in path.read_text(encoding='utf-8',errors='replace').splitlines():
        for label,marker in markers.items():
            if marker in line:
                stamp=re.search(r'20\d\d-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?',line)
                if stamp:
                    entries.append((stamp.group(),label))
                break
    print(name,entries[-120:])
