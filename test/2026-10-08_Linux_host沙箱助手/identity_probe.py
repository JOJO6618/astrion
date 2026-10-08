"""Ordinary-UID, Unix policy, and development-tool probes for disposable data."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

spec = importlib.util.spec_from_file_location("base_probe", "/probe.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
config = json.loads(sys.argv[1])
base.main()
workspace = Path(config["workspace"])
result = {"uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(), "groups": os.getgroups()}
status = Path("/proc/self/status").read_text()
result["status"] = {line.split(":", 1)[0]: line.split(":", 1)[1].strip()
                    for line in status.splitlines()
                    if line.split(":", 1)[0] in {"Uid", "Gid", "CapEff", "CapPrm", "CapBnd", "NoNewPrivs", "Seccomp"}}
result["apparmor"] = base.outcome(lambda: Path("/proc/self/attr/current").read_text().strip())
result["root_private_read"] = base.outcome(lambda: (workspace / "root-private.txt").read_text())
result["root_private_chmod"] = base.outcome(lambda: os.chmod(workspace / "root-private.txt", 0o644))
result["home_write"] = base.outcome(lambda: (Path(os.environ["HOME"]) / "marker.txt").write_text("private-home"))
result["home_owner"] = Path(os.environ["HOME"]).stat().st_uid

def command(args):
    output = subprocess.run(args, text=True, capture_output=True, timeout=15)
    return {"returncode": output.returncode, "stdout": output.stdout[-1500:], "stderr": output.stderr[-1500:]}

result["node"] = command(["node", "--version"])
result["node_worker"] = command(["node", "-e", "const {Worker}=require('worker_threads'); new Worker(\"require('worker_threads').parentPort.postMessage('worker-ok')\",{eval:true}).once('message',value=>console.log(value));"])
result["npm"] = command(["npm", "--version"])
result["profile_escape"] = command(["aa-exec", "-p", "unconfined", "--", "/usr/bin/true"])
if not config["readonly"]:
    result["git_init"] = command(["git", "init", "--quiet", str(workspace / "dev-repo")])
    result["git_status"] = command(["git", "-C", str(workspace / "dev-repo"), "status", "--short"])
    result["venv_create"] = command(["python3", "-m", "venv", "--without-pip", str(workspace / "dev-venv")])
    if result["venv_create"]["returncode"] == 0:
        result["venv_python"] = command([str(workspace / "dev-venv/bin/python"), "-c", "print('venv-ok')"])
    result["created_file_owner"] = (workspace / "written.txt").stat().st_uid
print(json.dumps({"identity_probe": result}, sort_keys=True), flush=True)
