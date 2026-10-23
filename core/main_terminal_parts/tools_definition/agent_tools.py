"""Named, resumable background child tools for every conversation."""
from modules.sub_agent.definitions import build_master_tools


class ToolsDefinitionAgentToolsMixin:
    def _build_agent_tools(self) -> list[dict]:
        return build_master_tools()
