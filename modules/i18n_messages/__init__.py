"""Per-domain backend i18n message packs.

Each module in this package exposes a module-level `MESSAGES` dict:

    MESSAGES = {
        "<domain>.<semantic_key>": {
            "zh-CN": "中文文案 {param}",
            "en-US": "English copy {param}",
        },
    }

All packs are auto-discovered and merged by modules/i18n.py at import time.
Message pack modules must be pure data — do not import modules.i18n here.
"""
