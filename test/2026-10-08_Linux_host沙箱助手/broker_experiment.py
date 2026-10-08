"""Finite remote harness. Creates only fixtures, its broker, units and profiles."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / "helper"
sys.path.insert(0, str(HELPER))
from profiles import profile_text
from seccomp import export_policy


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, timeout=35, **kwargs)


def main():
    os.umask(0o022)
    for folder in (ROOT / "runtime", ROOT / "jobs", ROOT / "workspace", ROOT / "outside", ROOT / "authorized"):
        folder.mkdir(mode=0o755)
    for folder in (ROOT / "workspace", ROOT / "outside", ROOT / "authorized"):
        os.chown(folder, 65534, 65534)
        (folder / "marker").write_text(folder.name)
        os.chown(folder / "marker", 65534, 65534)
    (ROOT / "workspace" / "escape").symlink_to(ROOT / "outside" / "marker")
    (ROOT / "workspace" / "root-private").write_text("synthetic-private")
    (ROOT / "workspace" / "root-private").chmod(0o600)
    run(["cc", "-Wall", "-Wextra", "-Werror", "-O2", str(HELPER / "uid_exec.c"), "-o", str(HELPER / "uid-exec")], check=True)
    export_policy(HELPER / "policy.bpf")
    export_policy(HELPER / "policy-none.bpf", network="none")
    profile = ROOT / "profiles.apparmor"
    profile.write_text(profile_text())
    import shutil
    config = {"protocol": 1, "allowed_uids": [65534], "socket": str(ROOT / "runtime" / "control.sock"),
              "runtime": str(ROOT / "jobs"), "install_root": str(HELPER),
              "bwrap": str(ROOT.parent / "tools/usr/bin/bwrap"), "max_jobs": 8, "runtime_max_sec": 120}
    for name, executable in (("python", "python3"), ("systemd_run", "systemd-run"),
                             ("systemctl", "systemctl"), ("bpftool", "bpftool")):
        config[name] = str(Path(shutil.which(executable)).resolve())
    config_path = ROOT / "helper.json"
    config_path.write_text(json.dumps(config))
    config_path.chmod(0o600)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    abstract = socket.socket(socket.AF_UNIX)
    abstract.bind("\0astrion-broker-exp-20261008")
    abstract.listen(16)
    path_socket = socket.socket(socket.AF_UNIX)
    path_socket.bind(str(ROOT / "workspace" / "fixture.sock"))
    (ROOT / "workspace" / "fixture.sock").chmod(0o666)
    path_socket.listen(16)
    results = {"cases": []}
    broker = None
    loaded = False
    def request(command, network="restricted", readonly=False, reads=None, writes=None):
        return {"operation": "run", "workspace": str(ROOT / "workspace"), "cwd": str(ROOT / "workspace"),
                "readonly": readonly, "workspace_only": True if not writes else False,
                "reads": reads or [], "writes": writes or [], "network": network,
                "argv": ["/bin/bash", "-c", command], "env": {}, "umask": 18}
    def client(req):
        encoded = base64.b64encode(json.dumps(req).encode()).decode()
        return ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", config["python"], "-I",
                str(HELPER / "client.py"), "--socket", config["socket"], "--request", encoded]
    try:
        run(["apparmor_parser", "--skip-cache", "-a", str(profile)], check=True)
        loaded = True
        broker = subprocess.Popen([config["python"], "-I", str(HELPER / "server.py"), "--config", str(config_path)],
                                  stdout=(ROOT / "broker.log").open("w"), stderr=subprocess.STDOUT)
        for _ in range(60):
            if Path(config["socket"]).exists() or broker.poll() is not None:
                break
            time.sleep(0.1)
        for network, readonly in (("full", False), ("restricted", False), ("restricted", True), ("none", False)):
            source = '''import json,os,socket,subprocess
r={"uid":os.getuid(),"gid":os.getgid(),"cwd":os.getcwd(),"label":open('/proc/self/attr/current').read().strip()}
open(os.environ['HOME']+'/home-check','w').write('private')
r['home_owner']=os.stat(os.environ['HOME']+'/home-check').st_uid
r['home_write']=True
for k,p in [("outside","../outside/marker"),("escape","escape"),("private","root-private"),("authorized",AUTHOR)]:
 try:r[k]=open(p).read()
 except OSError as e:r[k]=e.errno
try:open('written','w').write('own');r['write']=True;r['owner']=os.stat('written').st_uid
except OSError as e:r['write']=False;r['write_errno']=e.errno
for k,f,a in [("localhost",socket.AF_INET,('127.0.0.1',PORT)),("path",socket.AF_UNIX,SOCK),("abstract",socket.AF_UNIX,'\\0astrion-broker-exp-20261008')]:
 s=None
 try:s=socket.socket(f);s.settimeout(.5);s.connect(a);r[k]=True
 except OSError as e:r[k]=False;r[k+'_errno']=e.errno
 finally:
  if s:s.close()
r['socketpair']=False
try:a,b=socket.socketpair();a.send(b'x');r['socketpair']=b.recv(1)==b'x';a.close();b.close()
except OSError:pass
r['node']=subprocess.call(['node','-e',"const {Worker}=require('worker_threads');new Worker('1+1',{eval:true})"])
r['npm']=subprocess.call(['npm','--version'],stdout=subprocess.DEVNULL)
r['git']=subprocess.call(['git','--version'],stdout=subprocess.DEVNULL)
if not READONLY:
 r['venv']=subprocess.call(['python3','-m','venv','.venv'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 r['venv_python']=subprocess.call(['.venv/bin/python','-c','import ssl'],stdout=subprocess.DEVNULL)
r['status']={k:v.strip() for k,v in (line.split(':',1) for line in open('/proc/self/status') if ':' in line) if k in ['CapEff','CapPrm','CapBnd','NoNewPrivs','Seccomp']}
print(json.dumps(r))'''
            source = source.replace("AUTHOR", repr(str(ROOT / "authorized" / "marker"))).replace("PORT", str(listener.getsockname()[1])).replace("SOCK", repr(str(ROOT / "workspace" / "fixture.sock"))).replace('READONLY', repr(readonly))
            import shlex
            output = run(client(request("python3 -c " + shlex.quote(source), network, readonly,
                                        reads=[str(ROOT / "authorized")])) )
            record = {"network": network, "readonly": readonly, "code": output.returncode,
                      "stderr": output.stderr, "stdout": output.stdout}
            try:
                record["probe"] = json.loads(output.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                pass
            results["cases"].append(record)
            print(json.dumps(record), flush=True)
        # Mounting the control socket cannot grant an already-confined process
        # another sandbox: kernel-attested AppArmor identity rejects it.
        nested_source = '''import json,socket
s=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET);s.connect(SOCKET)
s.send(b'{"operation":"status"}')
try:r=json.loads(s.recv(65536));denied=bool(r.get('error'))
except (OSError,ValueError):denied=True
assert denied
print(json.dumps({'nested_refused':denied}))'''.replace('SOCKET', repr(config['socket']))
        import shlex
        nested = run(client(request('python3 -c ' + shlex.quote(nested_source), reads=[config['socket']])))
        results['nested_request'] = {'code': nested.returncode, 'stdout': nested.stdout, 'stderr': nested.stderr}
        interactive_request = request('unused')
        interactive_request['argv'] = ['/bin/bash', '-i']
        interactive = run(client(interactive_request), input='echo terminal-check; exit\n')
        results['terminal_pipe'] = {'code': interactive.returncode, 'stdout': interactive.stdout, 'stderr': interactive.stderr}
        # DAC rejection before root mounts, and symlink source refusal.
        denied = run(client(request("true", reads=[str(ROOT / "workspace" / "root-private")])))
        # O_PATH can pin an unreadable regular file; runtime DAC still prevents
        # reading it. A source with inaccessible parent must fail to pin.
        secret = ROOT / "private-directory"
        secret.mkdir(mode=0o700)
        (secret / "marker").write_text("fixture")
        denied_parent = run(client(request("true", reads=[str(secret / "marker")])))
        symlink = run(client(request("true", reads=[str(ROOT / "workspace" / "escape")])))
        results["source_checks"] = {"private_parent": denied_parent.returncode, "symlink": symlink.returncode}
        # Client SIGKILL must kill a setsid grandchild in its dedicated cgroup.
        task = subprocess.Popen(client(request("setsid sh -c 'echo $$ > child.pid; sleep 90' & wait")),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if (ROOT / "workspace" / "child.pid").exists():
                break
            time.sleep(.1)
        active_jobs = list((ROOT / "jobs").iterdir())
        host_pids = []
        for job in active_jobs:
            if not job.is_dir():
                continue
            cgroup = Path('/sys/fs/cgroup/system.slice') / ('astrion-sandbox-job-' + job.name + '.service')
            for pidfile in cgroup.rglob('cgroup.procs'):
                host_pids.extend(int(pid) for pid in pidfile.read_text().split())
        task.kill(); task.wait(timeout=5)
        for _ in range(80):
            if host_pids and not any(Path(f"/proc/{pid}").exists() for pid in host_pids):
                break
            time.sleep(.1)
        results["cancel_grandchild_gone"] = bool(host_pids) and not any(Path(f"/proc/{pid}").exists() for pid in host_pids)
    finally:
        if broker:
            broker.terminate()
            broker.wait(timeout=18)
        if loaded:
            output = run(["apparmor_parser", "--skip-cache", "-R", str(profile)])
            results["profile_unload"] = output.returncode
        listener.close(); abstract.close(); path_socket.close()
        results["jobs_remaining"] = [path.name for path in (ROOT / "jobs").iterdir() if path.is_dir()]
        (ROOT / "results.json").write_text(json.dumps(results, indent=2))
    return 0 if (len(results["cases"]) == 4 and all(case["code"] == 0 for case in results["cases"])
                 and results.get("cancel_grandchild_gone") and results.get('nested_request', {}).get('code') == 0
                 and results.get('terminal_pipe', {}).get('code') == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
