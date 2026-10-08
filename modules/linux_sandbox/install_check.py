"""Small disposable end-to-end install checks as the authorized OS account."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def verify_install(config: dict, account) -> None:
    directory = Path(tempfile.mkdtemp(prefix="astrion-sandbox-check-", dir="/var/tmp"))
    directory.chmod(0o755)
    workspace = directory / "workspace"
    workspace.mkdir(mode=0o755)
    os.chown(workspace, account.pw_uid, account.pw_gid)
    try:
        for attempt in range(30):
            if Path(config["socket"]).exists():
                break
            time.sleep(0.1)
        source = """import json,os,socket
assert os.getuid()==UID
s={k:v.strip() for k,v in (line.split(':',1) for line in open('/proc/self/status') if ':' in line)}
assert all(int(s[k],16)==0 for k in ['CapPrm','CapEff','CapBnd'])
assert s['NoNewPrivs']=='1' and s['Seccomp']=='2'
a,b=socket.socketpair();a.send(b'x');assert b.recv(1)==b'x'
open(os.environ['HOME']+'/home-check','w').write('private')
assert os.stat(os.environ['HOME']+'/home-check').st_uid==UID
try:open('write-check','w').write('own');written=True
except PermissionError:written=False
except OSError as e:
 if e.errno!=30:raise
 written=False
assert written==WRITABLE
print(json.dumps({'ok':True,'uid':os.getuid()}))
"""
        for network, readonly in (("full", False), ("restricted", False), ("restricted", True), ("none", False)):
            program = source.replace("UID", str(account.pw_uid)).replace("WRITABLE", repr(not readonly))
            request = {"operation": "run", "workspace": str(workspace), "cwd": str(workspace),
                       "reads": [], "writes": [], "readonly": readonly, "workspace_only": True,
                       "network": network, "argv": ["/usr/bin/python3", "-c", program], "env": {}, "umask": 0o022}
            encoded = base64.b64encode(json.dumps(request).encode()).decode()
            command = ["setpriv", "--reuid=" + str(account.pw_uid), "--regid=" + str(account.pw_gid), "--init-groups",
                       config["python"], "-I", config["install_root"] + "/client.py", "--request", encoded]
            output = subprocess.run(command, capture_output=True, text=True, timeout=40,
                                    env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
            if output.returncode or not json.loads(output.stdout.strip()).get("ok"):
                raise RuntimeError(f"Sandbox install check failed ({network}, readonly={readonly}): {output.stderr.strip()}")
    finally:
        shutil.rmtree(directory)
