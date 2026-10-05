"""Inject Esc only into our own foreground probe; the native hook ignores injected keys."""
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[2]
log = root / 'cache/windows-quick-smoke/escape.jsonl'
start = log.stat().st_size if log.exists() else 0
ctypes.windll.kernel32.SetErrorMode(0x8003)
capture = '--capture-probe' in sys.argv
args = [str(root / 'desktop/src-tauri/target/debug/examples/quick_smoke.exe'), '--interactive']
if capture:
    args.append('--capture-probe')
process = subprocess.Popen(args, cwd=root / 'desktop/src-tauri', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(10 if capture else 6)
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    owner = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(owner))
    if owner.value != process.pid:
        raise RuntimeError('Our probe is not foreground; no key was injected.')
    user32.keybd_event(0x1B, 0, 0, 0)
    user32.keybd_event(0x1B, 0, 2, 0)
    time.sleep(1)
    with log.open('rb') as stream:
        stream.seek(start)
        rows = [json.loads(line) for line in stream if line.strip()]
    hidden = any(row['event'] == 'hide' and row['data'].get('visible') for row in rows)
    result = {'foreground_pid_matches': True, 'dom_escape_requested_hide': hidden, 'events': rows}
    (root / 'cache/windows-quick-smoke/keyboard-focus-probe.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    if not hidden:
        raise RuntimeError('Injected Esc did not reach the normal DOM hide handler.')
finally:
    process.terminate()
    process.wait(timeout=5)
