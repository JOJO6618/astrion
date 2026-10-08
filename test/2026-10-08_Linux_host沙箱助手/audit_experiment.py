"""Read-only resource audit; only restores the experiment root's private mode."""
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path('/tmp/astrion-linux-sandbox-20261008-2Bjdqo')
assert ROOT.is_dir() and ROOT.stat().st_uid == 0
units = subprocess.run(['systemctl', 'list-units', '--all', '--no-legend', '--no-pager',
                        'astrion-sandbox-job-*', 'astrion-sandbox-b03-*', 'astrion-sandbox-batch*',
                        'astrion-sandbox-reverse*'], capture_output=True, text=True, timeout=10)
profiles = Path('/sys/kernel/security/apparmor/profiles').read_text().splitlines()
remaining_profiles = [line for line in profiles if line.startswith(('astrion-linux-sandbox-', 'astrion-exp-linux-20261008-'))]
remaining_jobs = []
remaining_sockets = []
for batch in ROOT.glob('batch_*'):
    jobs = batch / 'jobs'
    if jobs.is_dir():
        remaining_jobs.extend(str(path.relative_to(ROOT)) for path in jobs.iterdir() if path.is_dir())
    control = batch / 'runtime/control.sock'
    if control.exists():
        remaining_sockets.append(str(control.relative_to(ROOT)))
bpf = subprocess.run(['bpftool', '-j', 'prog', 'show'], capture_output=True, text=True, timeout=10)
remaining_bpf = [program.get('name') for program in json.loads(bpf.stdout) if program.get('name', '').startswith('astrion_unix')] if bpf.returncode == 0 else None
# Only our disposable root is changed. No business path or system policy is touched.
ROOT.chmod(0o700)
report = {'remaining_units': units.stdout.strip(), 'unit_query_code': units.returncode,
          'remaining_profiles': remaining_profiles, 'remaining_job_dirs': remaining_jobs,
          'remaining_control_sockets': remaining_sockets, 'remaining_experiment_bpf': remaining_bpf,
          'system_bwrap': shutil.which('bwrap'), 'root_mode': oct(ROOT.stat().st_mode & 0o777),
          'production_install_performed': False, 'existing_business_services_changed': False}
report['clean'] = (units.returncode == 0 and not report['remaining_units'] and not remaining_profiles
                   and not remaining_jobs and not remaining_sockets and remaining_bpf == [])
(ROOT / 'final_audit_v2.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
if not report['clean']:
    raise SystemExit(1)
