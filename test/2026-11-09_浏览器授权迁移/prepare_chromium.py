"""Download the official isolated test Chromium using Windows' trusted TLS roots."""
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / '_experiments/windows-browser-20261109/playwright-browsers'
URL = 'https://cdn.playwright.dev/builds/cft/149.0.7827.55/win64/chrome-win64.zip'
archive = DEST / 'chrome-win64.zip'
target = DEST / 'chromium-1228'
DEST.mkdir(parents=True, exist_ok=True)
if (target / 'chrome-win64/chrome.exe').is_file():
    print('Official isolated Chromium already present')
else:
    # Default SSL context validates the server chain; never disable verification.
    with urllib.request.urlopen(URL, timeout=120) as source, archive.open('wb') as output:
        shutil.copyfileobj(source, output)
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None
        assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in bundle.namelist())
        bundle.extractall(target)
    assert (target / 'chrome-win64/chrome.exe').is_file()
    print('Verified ZIP and extracted official isolated Chromium', archive.stat().st_size)
