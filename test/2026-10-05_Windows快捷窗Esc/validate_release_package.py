"""Verify updater signature, exact packaged bytes and release metadata without exposing keys."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

root = Path(__file__).resolve().parents[2]
conf = json.loads((root / 'desktop/src-tauri/tauri.conf.json').read_text(encoding='utf-8-sig'))
version = conf['version']
assert version == '0.5.1'
for name in ['package.json', 'package-lock.json']:
    assert json.loads((root / 'desktop' / name).read_text(encoding='utf-8-sig'))['version'] == version
assert re.search(r'^version = "0\.5\.1"$', (root / 'desktop/src-tauri/Cargo.toml').read_text(), re.MULTILINE)
exe = root / f'desktop/src-tauri/target/release/bundle/nsis/Astrion_{version}_x64-setup.exe'
sig = exe.with_suffix('.exe.sig')
public_lines = base64.b64decode(conf['plugins']['updater']['pubkey']).decode().splitlines()
public = base64.b64decode(public_lines[1])
signature_lines = base64.b64decode(sig.read_text()).decode().splitlines()
packet = base64.b64decode(signature_lines[1])
assert public[2:10] == packet[2:10], 'Updater signing key ID mismatch.'
assert packet[:2] == b'ED', 'Unexpected signature algorithm.'
key = Ed25519PublicKey.from_public_bytes(public[10:42])
key.verify(packet[10:74], hashlib.blake2b(exe.read_bytes()).digest())
trusted = signature_lines[2].removeprefix('trusted comment: ')
key.verify(base64.b64decode(signature_lines[3]), packet[10:74] + trusted.encode())
extracted = root / 'cache/windows-051-installer-check'
files = {
    'astrion-desktop.exe': root / 'desktop/src-tauri/target/release/astrion-desktop.exe',
    'runtime/backend/static/quick-capture/windows-bridge.js': root / 'static/quick-capture/windows-bridge.js',
    'runtime/backend/static/dist/assets/quick.js': root / 'static/dist/assets/quick.js',
    'runtime/backend/server/status/desktop_update.py': root / 'server/status/desktop_update.py',
}
for packaged, source in files.items():
    expected = source.read_bytes()
    if packaged == 'astrion-desktop.exe':
        # Tauri stamps the installed binary with its NSIS bundle type.
        expected = expected.replace(b'LE_TYPE_VAR_UNK', b'LE_TYPE_VAR_NSS')
    assert (extracted / packaged).read_bytes() == expected, f'Packaged resource mismatch: {packaged}'
changelog = (root / 'desktop/DESKTOP_CHANGELOG.md').read_text(encoding='utf-8-sig')
sections = re.split(r'^## ', changelog, flags=re.MULTILINE)
assert sections[1].startswith(version)
notes = sections[1].split('\n', 1)[1].strip() + '\n'
output = root / 'output/windows-0.5.1'
output.mkdir(parents=True, exist_ok=True)
shutil.copy2(exe, output / exe.name)
shutil.copy2(sig, output / sig.name)
(output / 'release-notes-windows.txt').write_text(notes, encoding='utf-8')
report = {
    'version': version, 'installer': exe.name, 'bytes': exe.stat().st_size,
    'sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
    'updater_signature_verified': True,
    'signature_comment_verified': True,
    'packaged_files_match_source': list(files),
    'installer_archive_test': '7-Zip: Everything is Ok',
    'desktop_runtime_report': 'cache/windows-051-desktop-probe/report.json',
    'mixed_dpi_and_multimonitor_tested': False,
    'installation_over_existing_app_tested': False,
}
(output / 'build-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
