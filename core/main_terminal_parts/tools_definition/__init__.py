from core.main_terminal_parts.tools_definition.base import ToolsDefinitionBaseMixin
from core.main_terminal_parts.tools_definition.custom_mcp import ToolsDefinitionCustomMcpMixin
from core.main_terminal_parts.tools_definition.core_tools import ToolsDefinitionCoreToolsMixin
from core.main_terminal_parts.tools_definition.file_tools import ToolsDefinitionFileToolsMixin
from core.main_terminal_parts.tools_definition.terminal_tools import ToolsDefinitionTerminalToolsMixin
from core.main_terminal_parts.tools_definition.search_web_tools import ToolsDefinitionSearchWebToolsMixin
from core.main_terminal_parts.tools_definition.agent_tools import ToolsDefinitionAgentToolsMixin
from core.main_terminal_parts.tools_definition.context_tools import ToolsDefinitionContextToolsMixin
from core.main_terminal_parts.tools_definition.misc_tools import ToolsDefinitionMiscToolsMixin
from core.main_terminal_parts.tools_definition.workflow_tools import ToolsDefinitionWorkflowToolsMixin
from core.main_terminal_parts.tools_definition.main import ToolsDefinitionMainMixin

class MainTerminalToolsDefinitionMixin(
    ToolsDefinitionBaseMixin,
    ToolsDefinitionCustomMcpMixin,
    ToolsDefinitionCoreToolsMixin,
    ToolsDefinitionFileToolsMixin,
    ToolsDefinitionTerminalToolsMixin,
    ToolsDefinitionSearchWebToolsMixin,
    ToolsDefinitionAgentToolsMixin,
    ToolsDefinitionContextToolsMixin,
    ToolsDefinitionMiscToolsMixin,
    ToolsDefinitionWorkflowToolsMixin,
    ToolsDefinitionMainMixin,
):
    pass
