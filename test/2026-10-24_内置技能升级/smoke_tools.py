"""Small local smoke checks; creates only temporary synthetic input/output files."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "agentskills/docx/scripts"
sys.path.insert(0, str(SCRIPTS))


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=80)
    if result.returncode:
        raise RuntimeError(result.stderr[-1500:] or result.stdout[-1500:])
    return result.stdout


def main():
    checks = {}
    with tempfile.TemporaryDirectory(prefix="skills-smoke-", dir=ROOT / "cache") as temp:
        folder = Path(temp)
        # Real encode, metadata inspection and decode without external media.
        movie = folder / "sample.mp4"
        run(["ffmpeg", "-v", "error", "-n", "-f", "lavfi", "-i",
             "testsrc2=size=160x120:rate=10", "-t", "0.5", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", str(movie)])
        metadata = json.loads(run(["ffprobe", "-v", "error", "-show_streams",
                                   "-show_format", "-of", "json", str(movie)]))
        stream = metadata["streams"][0]
        assert (stream["width"], stream["height"], stream["codec_name"]) == (160, 120, "h264")
        run(["ffmpeg", "-v", "error", "-i", str(movie), "-f", "null", "-"])
        checks["ffmpeg_encode_probe_decode"] = "passed: 160x120 H.264 synthetic video"

        # Minimal valid OOXML containing one insertion and one deletion.
        source, output = folder / "tracked.docx", folder / "accepted.docx"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            archive.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:del w:id="1" w:author="Test" w:date="2026-10-24T00:00:00Z"><w:r><w:delText>old</w:delText></w:r></w:del><w:ins w:id="2" w:author="Test" w:date="2026-10-24T00:00:00Z"><w:r><w:t>new</w:t></w:r></w:ins></w:p><w:sectPr/></w:body></w:document>')
        spec = importlib.util.spec_from_file_location("smoke_accept_changes", SCRIPTS / "accept_changes.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _, message = module.accept_changes(str(source), str(output))
        checks["libreoffice_accept_changes"] = message
        if message.startswith("Successfully"):
            module._verify_no_revisions(output)
            with zipfile.ZipFile(output) as archive:
                body = archive.read("word/document.xml").decode()
                assert ">new<" in body and ">old<" not in body
            checks["libreoffice_accept_changes"] = "passed: real LibreOffice removed revisions, kept new text"
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks["libreoffice_accept_changes"].startswith("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
