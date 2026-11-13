"""Assemble a Windows portable delivery only from a successfully tested package."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "output/windows-electron"


def sha256(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    release = json.loads((ROOT / "desktop-electron/windows-release.json").read_text("utf-8"))
    version = release["version"]
    source = OUTPUT / "build/win-unpacked"
    smoke = json.loads((OUTPUT / "packaged-smoke-latest.json").read_text("utf-8"))
    if not smoke.get("success"):
        raise RuntimeError("The latest packaged smoke test did not pass")
    artifacts = smoke["artifacts"]
    for key, file in [("executable_sha256", source / "astrion-desktop.exe"),
                      ("asar_sha256", source / "resources/app.asar")]:
        if sha256(file) != artifacts[key]:
            raise RuntimeError(f"Package changed after verification: {file.name}")
    delivery = OUTPUT / "delivery" / f"Astrion-{version}-Windows-Electron"
    resume = "--resume" in sys.argv[1:]
    refresh = "--refresh-receipt" in sys.argv[1:]
    preserve_previous = "--archive-previous" in sys.argv[1:]
    archive = OUTPUT / f"Astrion-{version}-windows-x64-portable.zip"
    if preserve_previous:
        if refresh or resume:
            raise ValueError("--archive-previous cannot be combined with resume/refresh")
        if (delivery / "validation-profile").exists():
            raise RuntimeError("Previous delivery has user validation data; refusing to move it")
        previous = OUTPUT / "previous-validated" / f"Astrion-{version}-before-silent-update"
        previous.mkdir(parents=True, exist_ok=False)
        for artifact in [delivery, archive, OUTPUT / "build-receipt.json", OUTPUT / "SHA256SUMS.txt"]:
            if artifact.exists():
                if artifact.is_file() and artifact != archive:
                    shutil.copy2(artifact, previous / artifact.name)
                else:
                    artifact.rename(previous / artifact.name)
        print(f"Previous verified delivery preserved in {previous}", flush=True)
    if refresh:
        for key, file in [("executable_sha256", delivery / "App/astrion-desktop.exe"),
                          ("asar_sha256", delivery / "App/resources/app.asar")]:
            if sha256(file) != artifacts[key]:
                raise RuntimeError("Portable application differs from verified package")
    else:
        if delivery.exists() and not resume:
            raise FileExistsError(f"Refusing to replace an existing delivery: {delivery}")
        if (delivery / "validation-profile").exists():
            raise RuntimeError("Delivery has user validation data; refusing to replace it")
        delivery.mkdir(parents=True, exist_ok=resume)
        print("Copying verified application files", flush=True)
        shutil.copytree(source, delivery / "App", dirs_exist_ok=resume)
        templates = ROOT / "desktop-electron/build"
        for name in ["start-validation.cmd", "start-existing-data.cmd", "WINDOWS_DELIVERY_README.txt"]:
            text = (templates / name).read_text("utf-8").replace("{version}", version)
            (delivery / name).write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
        print("Creating portable ZIP", flush=True)
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
            for file in sorted(delivery.rglob("*")):
                if file.is_file():
                    zipped.write(file, str(Path(delivery.name) / file.relative_to(delivery)))
    with zipfile.ZipFile(archive) as zipped:
        failed = zipped.testzip()
        if failed:
            raise RuntimeError(f"ZIP integrity check failed: {failed}")
    installer = OUTPUT / "build" / f"Astrion-{version}-windows-x64-setup.exe"
    receipt = json.loads((OUTPUT / "build-receipt.json").read_text("utf-8"))
    receipt.update(version=version, runtime_verified=True,
                   executable_sha256=artifacts["executable_sha256"],
                   executable_bytes=(source / "astrion-desktop.exe").stat().st_size,
                   asar_sha256=artifacts["asar_sha256"], smoke_evidence=smoke["output"],
                   built_at=datetime.fromtimestamp((source / "resources/app.asar").stat().st_mtime, timezone.utc).isoformat(),
                   verified_at=datetime.now(timezone.utc).isoformat(),
                   unpacked_bytes=sum(f.stat().st_size for f in source.rglob("*") if f.is_file()))
    receipt["delivery"] = [{"path": str(file.relative_to(ROOT)).replace("\\", "/"),
                            "bytes": file.stat().st_size, "sha256": sha256(file)}
                           for file in [installer, archive]]
    hook_hash = sha256(ROOT / "desktop-electron/build/installer-windows.nsh")
    receipt.pop("installer_lifecycle_test", None)
    receipt.pop("tauri_to_electron_test", None)
    current_install_evidence = False
    for filename, key, product, hash_key in [
        ("installer-test/installed-update-results.json", "installer_lifecycle_test", "com.astrion.electron-migration-test", "nsisHookSha256"),
        ("cross-shell/results.json", "tauri_to_electron_test", "com.astrion.crossshell-test", "nsisHook"),
    ]:
        evidence_file = OUTPUT / filename
        if not evidence_file.is_file():
            continue
        evidence = json.loads(evidence_file.read_text("utf-8"))
        if evidence.get("success") and evidence.get("buildInputs", {}).get(hash_key) == hook_hash:
            receipt[key] = {
                "success": True,
                "evidence": str(evidence_file.relative_to(ROOT)).replace("\\", "/"),
                "product": product, "checks": len(evidence["checks"]),
                "nsis_hook_sha256": hook_hash, "production_installation_tested": False,
            }
            current_install_evidence = True
    if (refresh or preserve_previous) and not current_install_evidence:
        raise RuntimeError("No successful installer test matches the current NSIS hooks")
    (OUTPUT / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", "utf-8")
    (OUTPUT / "SHA256SUMS.txt").write_text("".join(
        f"{item['sha256']}  {Path(item['path']).relative_to(Path('output/windows-electron')).as_posix()}\n"
        for item in receipt["delivery"]), "utf-8")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
