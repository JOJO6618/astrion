"""Shared browser tool schemas for desktop main-Agent conversations."""

import json
from pathlib import Path
from typing import Dict, List


class ToolsDefinitionBrowserToolsMixin:
    def _build_browser_tools(self) -> List[Dict]:
        from modules.native_browser.availability import native_browser_supported

        if not native_browser_supported():
            return []
        path = Path(__file__).with_name("browser_tools.json")
        definitions = json.loads(path.read_text(encoding="utf-8"))
        for tool in definitions:
            parameters = tool["function"]["parameters"]
            parameters["properties"] = self._inject_intent(parameters["properties"])
        return definitions
