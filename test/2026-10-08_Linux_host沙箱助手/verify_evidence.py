"""Re-check stored remote evidence; does not contact the server."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
report = json.loads((ROOT / "broker_results_v3.json").read_text())
checks = 0


def expect(value, description):
    global checks
    checks += 1
    if not value:
        raise AssertionError(description)


expect(len(report["cases"]) == 4, "four execution combinations")
for case in report["cases"]:
    probe = case["probe"]
    label = f"{case['network']}, readonly={case['readonly']}"
    expect(case["code"] == 0, label + " command success")
    expect(probe["uid"] == probe["gid"] == 65534, label + " actual OS identity")
    expect(probe["home_write"] and probe["home_owner"] == 65534, label + " private home ownership")
    expect(probe["outside"] == 2 and probe["escape"] == 2, label + " hidden outside data")
    expect(probe["private"] == 13, label + " root-private file DAC denial")
    expect(probe["authorized"] == "authorized", label + " authorized reads")
    expect(probe["write"] == (not case["readonly"]), label + " write permission")
    if not case["readonly"]:
        expect(probe["owner"] == 65534, label + " file owner")
        expect(probe["venv"] == probe["venv_python"] == 0, label + " virtual environment")
    else:
        expect(probe["write_errno"] == 30, label + " readonly mount refusal")
    expect(probe["localhost"] == (case["network"] != "none"), label + " IP policy")
    expect(probe["path"] == (case["network"] != "none"), label + " pathname Unix policy")
    expect(probe["abstract"] == (case["network"] == "full"), label + " abstract Unix policy")
    expect(probe["socketpair"], label + " internal IPC")
    expect(probe["node"] == probe["npm"] == probe["git"] == 0, label + " toolchain")
    expect(all(int(probe["status"][name], 16) == 0 for name in ("CapPrm", "CapEff", "CapBnd")), label + " cleared caps")
    expect(probe["status"]["NoNewPrivs"] == "1" and probe["status"]["Seccomp"] == "2", label + " kernel protections")
    expect(probe["label"] == "astrion-linux-sandbox-" + case["network"] + " (enforce)", label + " inherited confinement")
expect(report["nested_request"]["code"] == 0 and json.loads(report["nested_request"]["stdout"])["nested_refused"], "nested helper request rejected")
expect(report["terminal_pipe"]["code"] == 0 and "terminal-check" in report["terminal_pipe"]["stdout"], "terminal input path")
expect(report["source_checks"]["private_parent"] == report["source_checks"]["symlink"] == 125, "source validation fails closed")
expect(report["cancel_grandchild_gone"], "SIGKILL client reaps cgroup descendants")
expect(report["profile_unload"] == 0 and report["jobs_remaining"] == [], "policy and job cleanup")
network = json.loads((ROOT.parent / "2026-10-08_Linux_host沙箱实验" / "report_v2.json").read_text())
expect(network["passed_checks"] == network["total_checks"] == 248, "initial network/file matrix")
print(f"Stored broker evidence: {checks}/{checks} checks passed; initial matrix: 248/248.")
