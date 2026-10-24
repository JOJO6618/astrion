"""Accept tracked changes using LibreOffice with an isolated per-call profile."""

import argparse
import logging
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from defusedxml import ElementTree
from office.soffice import get_soffice_env

logger = logging.getLogger(__name__)

ACCEPT_CHANGES_MACRO = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE script:module PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">
<script:module xmlns:script="http://openoffice.org/2000/script" script:name="Module1" script:language="StarBasic">
    Sub AcceptAllTrackedChanges()
        Dim document As Object
        Dim dispatcher As Object
        document = ThisComponent.CurrentController.Frame
        dispatcher = createUnoService("com.sun.star.frame.DispatchHelper")
        dispatcher.executeDispatch(document, ".uno:AcceptAllTrackedChanges", "", 0, Array())
        ThisComponent.store()
        ThisComponent.close(True)
    End Sub
</script:module>"""

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REVISION_TAGS = {
    WORD_NS + name for name in (
        "ins", "del", "moveFrom", "moveTo", "pPrChange", "rPrChange",
        "sectPrChange", "tblPrChange", "tblGridChange", "trPrChange",
        "tcPrChange", "tblPrExChange", "numberingChange", "cellIns",
        "cellDel", "cellMerge", "moveFromRangeStart", "moveFromRangeEnd",
        "moveToRangeStart", "moveToRangeEnd",
    )
}


def _verify_no_revisions(path: Path) -> None:
    """Reject silent macro failures instead of reporting an unchanged copy as clean."""
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                root = ElementTree.fromstring(archive.read(name))
                if any(node.tag in REVISION_TAGS for node in root.iter()):
                    raise ValueError(f"Tracked changes remain in {name}")


def _setup_libreoffice_macro(profile: Path) -> None:
    subprocess.run(
        ["soffice", "--headless", f"-env:UserInstallation={profile.as_uri()}",
         "--terminate_after_init"],
        capture_output=True, timeout=15, check=True, env=get_soffice_env(),
    )
    macro_dir = profile / "user" / "basic" / "Standard"
    macro_dir.mkdir(parents=True, exist_ok=True)
    (macro_dir / "Module1.xba").write_text(ACCEPT_CHANGES_MACRO, encoding="utf-8")


def accept_changes(input_file: str, output_file: str) -> tuple[None, str]:
    input_path = Path(input_file).resolve()
    output_path = Path(output_file).resolve()
    if not input_path.is_file():
        return None, f"Error: Input file not found: {input_file}"
    if input_path.suffix.lower() != ".docx":
        return None, f"Error: Input file is not a DOCX file: {input_file}"
    if input_path == output_path:
        return None, "Error: Use a separate output file to preserve the input"

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Keep temporary state in the selected writable output directory.
        with tempfile.TemporaryDirectory(prefix="astrion_lo_", dir=output_path.parent) as temp:
            temp_root = Path(temp).resolve()
            profile = temp_root / "profile"
            staged = temp_root / "document.docx"
            shutil.copy2(input_path, staged)
            _setup_libreoffice_macro(profile)
            subprocess.run(
                ["soffice", "--headless", f"-env:UserInstallation={profile.as_uri()}",
                 "--norestore",
                 "vnd.sun.star.script:Standard.Module1.AcceptAllTrackedChanges?language=Basic&location=application",
                 str(staged)],
                capture_output=True, text=True, timeout=60, check=True,
                env=get_soffice_env(),
            )
            _verify_no_revisions(staged)
            shutil.copy2(staged, output_path)
    except subprocess.TimeoutExpired:
        return None, "Error: LibreOffice timed out; accepting tracked changes was not verified"
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr or str(exc)
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", errors="replace")
        return None, f"Error: LibreOffice failed: {detail}"
    except Exception as exc:
        logger.warning("Accepting tracked changes failed: %s", exc)
        return None, f"Error: Failed to accept tracked changes: {exc}"
    return None, f"Successfully accepted all tracked changes: {input_file} -> {output_file}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Accept all tracked changes in a DOCX file")
    parser.add_argument("input_file", help="Input DOCX file with tracked changes")
    parser.add_argument("output_file", help="Separate output DOCX file (no tracked changes)")
    args = parser.parse_args()
    _, message = accept_changes(args.input_file, args.output_file)
    print(message)
    if message.startswith("Error"):
        raise SystemExit(1)
