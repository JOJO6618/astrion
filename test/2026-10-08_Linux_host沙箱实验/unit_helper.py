"""Open only the experiment-local BPF FD and replace this process with bwrap."""
import json
import os
from pathlib import Path
import subprocess
import sys

command = json.loads(sys.argv[1])
policy = Path(sys.argv[2]).resolve()
root = Path(__file__).resolve().parent
if policy.parent != root or Path(command[0]).resolve() != (root / "tools/usr/bin/bwrap").resolve():
    raise SystemExit("Experiment root mismatch")
if Path("/sys/fs/cgroup").exists():
    lines = Path("/proc/self/cgroup").read_text().splitlines()
    cgroup = next((line.split(":", 2)[2] for line in lines if line.startswith("0::")), "")
    if "astrion-sandbox-" in cgroup:
        result = subprocess.run(
            ["bpftool", "cgroup", "show", "/sys/fs/cgroup" + cgroup],
            capture_output=True, text=True, timeout=3,
        )
        metadata = {"cgroup": cgroup, "bpf_returncode": result.returncode,
                    "attached_programs": result.stdout, "stderr": result.stderr}
        (root / "results" / (Path(cgroup).name + ".json")).write_text(json.dumps(metadata, indent=2))
fd = os.open(policy, os.O_RDONLY)
os.set_inheritable(fd, True)
command = [str(fd) if token == "SECCOMP_FD" else token for token in command]
os.execv(command[0], command)
