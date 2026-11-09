"""Print only requested cached SDK declarations (Windows host)."""
from pathlib import Path
import sys

registry = Path.home() / '.cargo/registry/src'
for relative in ('webview2-com-sys-0.38.2/src/bindings.rs', 'webview2-com-0.38.2/src/lib.rs'):
    source = next(registry.glob('*/' + relative))
    lines = source.read_text(encoding='utf-8').splitlines()
    for index, line in enumerate(lines):
        if 'pub unsafe fn ' not in line:
            continue
        parent = next((lines[i] for i in range(index, max(-1, index - 1200), -1) if lines[i].startswith('impl ')), '')
        for query in sys.argv[1:]:
            interface, _, name = query.rpartition('.')
            if (not interface or parent == 'impl ' + interface + ' {') and (name == '*' or 'fn ' + name + '(' in line or 'fn ' + name + '<' in line):
                print(parent, '\n' + '\n'.join(lines[index:index + (1 if name == '*' else 6)]))
