"""Expose completion of the top-level intent string before other arguments finish."""
from __future__ import annotations

import json
from typing import Optional


def extract_complete_intent(arguments: str) -> Optional[str]:
    """Return a closed intent, or '' for a complete object without intent.

    None means no completion yet. Decode keys and preceding values structurally so nested keys, escaped quotes
    and braces inside strings cannot masquerade as a complete top-level intent.
    The remainder of the object is allowed to be incomplete.
    """
    decoder = json.JSONDecoder()
    source = arguments.lstrip()
    if not source.startswith('{'):
        return None
    offset = 1
    try:
        while offset < len(source):
            while offset < len(source) and source[offset].isspace():
                offset += 1
            if offset < len(source) and source[offset] == '}':
                # A complete object without intent can now use its tool name.
                decoder.decode(source)
                return ''
            key, offset = decoder.raw_decode(source, offset)
            if not isinstance(key, str):
                return None
            while offset < len(source) and source[offset].isspace():
                offset += 1
            if offset >= len(source) or source[offset] != ':':
                return None
            offset += 1
            while offset < len(source) and source[offset].isspace():
                offset += 1
            value, offset = decoder.raw_decode(source, offset)
            if key == 'intent':
                return value if isinstance(value, str) else None
            while offset < len(source) and source[offset].isspace():
                offset += 1
            if offset < len(source) and source[offset] == '}':
                decoder.decode(source)
                return ''
            if offset >= len(source) or source[offset] != ',':
                return None
            offset += 1
    except (ValueError, IndexError):
        return None
    return None
