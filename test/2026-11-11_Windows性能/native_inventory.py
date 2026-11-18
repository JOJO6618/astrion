"""Read-only Windows inventory; never emits conversation text or credentials."""
import json
import os
from pathlib import Path


def main():
    root = Path.home() / '.astrion' / 'astrion-desktop'
    settings = Path.home() / '.astrion' / 'rundata_settings.json'
    if settings.exists():
        print('settings_keys', list(json.loads(settings.read_text(encoding='utf-8'))))
    print('default_root', str(root), root.exists())
    for base in [root, Path(os.environ.get('LOCALAPPDATA', '')) / 'Astrion']:
        if not base.exists():
            continue
        print('root_entries', str(base), [p.name for p in base.iterdir()])
    data = root / 'host' / 'data'
    if data.exists():
        print('data_entries', [p.name for p in data.iterdir()])
        convs = sorted(data.glob('**/conv_*.json'), key=lambda p: p.stat().st_size, reverse=True)
        print('largest_conversations', [{'path': str(p), 'bytes': p.stat().st_size} for p in convs[:12]])
    logs = root / 'host' / 'logs'
    if logs.exists():
        print('logs', [{'name':p.name,'bytes':p.stat().st_size} for p in logs.iterdir() if p.is_file()])


if __name__ == '__main__':
    main()
