"""Publish only the approved Windows 0.10.0 installer from a staging directory."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

BASE = Path('/var/www/astrion/downloads')
NAMES = ('Astrion_0.10.0_x64-setup.exe', 'Astrion_0.10.0_x64-setup.exe.sig',
         'release-notes-windows.txt', 'latest.yml')
GENERATED = ('latest-windows-x86_64.json', 'latest-mac.json', 'downloads.json')


def digest(path: Path, algorithm: str = 'sha256') -> str:
    result = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest() if algorithm == 'sha256' else base64.b64encode(result.digest()).decode()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main() -> None:
    work = Path(__file__).resolve().parent
    expected = load(work / 'expected.json')
    payload = work / 'payload'
    for name in NAMES:
        if digest(payload / name) != expected['files'][name]:
            raise RuntimeError('Staged file hash differs: ' + name)
    installer = payload / NAMES[0]
    if installer.stat().st_size != expected['installerBytes'] or digest(installer, 'sha512') != expected['installerSha512']:
        raise RuntimeError('Staged installer size or SHA512 differs')
    yml = (payload / 'latest.yml').read_text(encoding='utf-8-sig')
    if 'version: 0.10.0' not in yml or 'path: ' + NAMES[0] not in yml or expected['installerSha512'] not in yml:
        raise RuntimeError('Electron update manifest differs')
    with (BASE / '.windows-publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if digest(BASE / NAMES[0]) != expected['previousInstallerSha256']:
            raise RuntimeError('The online installer changed since the approved baseline; publication stopped')
        previous_mac = load(BASE / 'latest-mac.json')
        previous_mac_download = load(BASE / 'downloads.json')['platforms'].get('macos-arm64')
        backup = work / 'backup'
        backup.mkdir(exist_ok=False)
        for name in (*NAMES, *GENERATED):
            if (BASE / name).exists():
                shutil.copy2(BASE / name, backup / name)
        changed = []
        try:
            for name in NAMES:
                os.chmod(payload / name, 0o644)
                os.replace(payload / name, BASE / name)
                changed.append(name)
            regen = subprocess.run(['python3', str(BASE / 'regen_manifest.py')], text=True,
                                   capture_output=True, check=True)
            (work / 'regenerate.log').write_text(regen.stdout + regen.stderr, encoding='utf-8')
            windows = load(BASE / 'latest-windows-x86_64.json')
            downloads = load(BASE / 'downloads.json')
            target = windows['platforms']['windows-x86_64']
            if windows['version'] != '0.10.0' or target['signature'] != (BASE / NAMES[1]).read_text().strip():
                raise RuntimeError('Generated Tauri version or signature differs')
            if target['url'] != 'https://astrion.cyjai.com/downloads/' + NAMES[0]:
                raise RuntimeError('Generated Tauri installer URL differs')
            win_download = downloads['platforms']['windows-x64']
            if win_download['version'] != '0.10.0' or win_download['file'] != NAMES[0] or win_download['size_bytes'] != expected['installerBytes']:
                raise RuntimeError('Website Windows download entry differs')
            if load(BASE / 'latest-mac.json') != previous_mac or downloads['platforms'].get('macos-arm64') != previous_mac_download:
                raise RuntimeError('macOS update or download entry changed')
            if digest(BASE / NAMES[0]) != expected['installerSha256']:
                raise RuntimeError('Published installer hash differs')
            receipt = dict(success=True, published=True, version='0.10.0',
                           time=datetime.now(timezone.utc).isoformat(), backup=str(backup),
                           installerBytes=expected['installerBytes'], installerSha256=expected['installerSha256'],
                           installerSha512=expected['installerSha512'], macosUnchanged=True,
                           windowsManifestVerified=True, electronManifestVerified=True,
                           functionalUpdateTestRun=False)
            (work / 'server-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
            print(json.dumps(receipt))
        except BaseException:
            for name in changed:
                shutil.copy2(backup / name, BASE / name)
            # Regenerate from the actual artifacts rather than restoring a possibly
            # outdated macOS manifest if another platform published concurrently.
            rollback = subprocess.run(['python3', str(BASE / 'regen_manifest.py')],
                                      text=True, capture_output=True)
            (work / 'rollback.log').write_text(rollback.stdout + rollback.stderr, encoding='utf-8')
            raise


if __name__ == '__main__':
    main()
