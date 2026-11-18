"""0.10.3 发布后公网核对：只读 GET/HEAD，不修改服务器。"""
import json
import urllib.request

BASE = "https://astrion.cyjai.com/downloads/"


def get(path: str) -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return resp.read()


def head(path: str):
    request = urllib.request.Request(BASE + path, method="HEAD")
    with urllib.request.urlopen(request, timeout=30) as resp:
        return resp.status, resp.headers.get("Content-Length")


print("=== latest.yml ===")
for line in get("latest.yml").decode("utf-8").splitlines():
    if line.startswith(("version", "path")) or "url:" in line:
        print(" ", line.strip())

print("=== latest-windows-x86_64.json ===")
data = json.loads(get("latest-windows-x86_64.json"))
win = data["platforms"]["windows-x86_64"]
print("  version:", data["version"])
print("  pub_date:", data["pub_date"])
print("  url:", win["url"])
print("  signature:", win["signature"][:48] + "...")
print("  notes_len:", len(data["notes"]), "| 前60字:", data["notes"][:60])

print("=== downloads.json (windows-x64) ===")
downloads = json.loads(get("downloads.json"))
print("  mac version:", downloads.get("mac", {}).get("version"))
windows = downloads.get("windows-x64") or downloads.get("windows") or {}
print("  windows keys:", list(windows)[:8] if isinstance(windows, dict) else type(windows))
if isinstance(windows, dict):
    for key in ("version", "url", "size", "bytes"):
        if key in windows:
            print(f"  {key}:", windows[key])

print("=== 安装器 HEAD ===")
status, length = head("Astrion_0.10.3_x64-setup.exe")
print("  status:", status, "| bytes:", length, "| 期望 246741069:", str(length) == "246741069")

print("=== release-notes-windows.txt ===")
notes = get("release-notes-windows.txt").decode("utf-8")
print("  chars:", len(notes), "| 首行:", notes.splitlines()[0][:60])
