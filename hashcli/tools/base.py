"""Base tool interface for LLM tool calling."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from ..config import HashConfig


class Tool(ABC):
    """Abstract base class for all LLM tools."""

    @abstractmethod
    async def execute(self, arguments: Dict[str, Any], config: HashConfig) -> str:
        """Execute the tool with given arguments.

        Args:
            arguments: Dictionary of arguments passed from the LLM
            config: Hash configuration object

        Returns:
            String result of the tool execution
        """
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Get the tool name."""
        pass

    @abstractmethod
    def get_description(self) -> str:
        """Get the tool description."""
        pass

    def validate_arguments(self, arguments: Dict[str, Any]) -> bool:
        """Validate tool arguments. Override if needed."""
        return True

    def requires_confirmation(self) -> bool:
        """Whether this tool requires user confirmation. Override if needed."""
        return True


def get_tool_executor(tool_name: str) -> Optional[Tool]:
    """Get tool executor by name."""
    from .code_analysis import CodeAnalysisTool
    from .filesystem import FileSystemTool
    from .shell import ShellTool
    from .web_search import WebSearchTool

    tools = {
        "execute_shell_command": ShellTool(),
        "read_file": FileSystemTool(),
        "write_file": FileSystemTool(),
        "list_directory": FileSystemTool(),
        "web_search": WebSearchTool(),
        "analyze_code": CodeAnalysisTool(),
    }

    return tools.get(tool_name)
