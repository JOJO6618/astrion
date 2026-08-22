"""OCR / VLM 配置。"""

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


OCR_API_BASE_URL = _env("OCR_API_BASE_URL", "")
OCR_API_KEY = _env("OCR_API_KEY", "")
OCR_MODEL_ID = _env("OCR_MODEL_ID", "")
OCR_MAX_TOKENS = int(_env("OCR_MAX_TOKENS", "4096") or "4096")

__all__ = [
    "OCR_API_BASE_URL",
    "OCR_API_KEY",
    "OCR_MODEL_ID",
    "OCR_MAX_TOKENS",
]
