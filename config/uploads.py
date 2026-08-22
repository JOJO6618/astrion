"""上传隔离与扫描相关配置。"""

import os
import shlex

_DEFAULT_ALLOWED_EXTENSIONS = (
    ".txt,.md,.rst,.py,.js,.ts,.json,.yml,.yaml,.ini,.cfg,.conf,.ipynb"
    ".csv,.tsv,.log,.mdx,.env,.sh,.bat,.ps1,.sql,.html,.css,"
    ".svg,.png,.jpg,.jpeg,.gif,.bmp,.webp,.ico,.pdf,.pptx,.docx,"
    ".xlsx,.mp3,.wav,.flac,.mp4,.mov,.mkv,.avi,.webm,"
    ".zip,.tar,.tar.gz,.tar.bz2,.tgz,.tbz,.gz,.bz2,.xz,.7z"
    ",.com"
)


def _parse_extensions(raw: str):
    entries = []
    for chunk in (raw or "").split(","):
        token = chunk.strip().lower()
        if not token:
            continue
        if not token.startswith("."):
            token = f".{token}"
        entries.append(token)
    # 去重但保持顺序
    deduped = []
    for item in entries:
        if item not in deduped:
            deduped.append(item)
    return tuple(deduped)


UPLOAD_ALLOWED_EXTENSIONS = _parse_extensions(
    os.environ.get("UPLOAD_ALLOWED_EXTENSIONS", _DEFAULT_ALLOWED_EXTENSIONS)
)
UPLOAD_QUARANTINE_SUBDIR = os.environ.get("UPLOAD_QUARANTINE_SUBDIR", ".upload_quarantine")


def _parse_bool(value: str, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


# 默认关闭 ClamAV 扫描以降低常驻内存占用，可通过设置环境变量重新开启
UPLOAD_CLAMAV_ENABLED = _parse_bool(os.environ.get("UPLOAD_CLAMAV_ENABLED", "0"), default=False)
UPLOAD_CLAMAV_BIN = os.environ.get("UPLOAD_CLAMAV_BIN", "clamdscan")
UPLOAD_CLAMAV_ARGS = tuple(
    shlex.split(os.environ.get("UPLOAD_CLAMAV_ARGS", "--fdpass --no-summary --stdout"))
)
UPLOAD_CLAMAV_TIMEOUT_SECONDS = int(os.environ.get("UPLOAD_CLAMAV_TIMEOUT_SECONDS", "30"))
UPLOAD_SCAN_LOG_SUBDIR = os.environ.get("UPLOAD_SCAN_LOG_SUBDIR", "upload_guard")

__all__ = [
    "UPLOAD_ALLOWED_EXTENSIONS",
    "UPLOAD_QUARANTINE_SUBDIR",
    "UPLOAD_CLAMAV_ENABLED",
    "UPLOAD_CLAMAV_BIN",
    "UPLOAD_CLAMAV_ARGS",
    "UPLOAD_CLAMAV_TIMEOUT_SECONDS",
    "UPLOAD_SCAN_LOG_SUBDIR",
]
