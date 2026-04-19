"""Tool calling implementations for LLM integration."""

from .base import Tool, get_tool_executor
from .shell import ShellTool

__all__ = [
    "Tool",
    "get_tool_executor",
    "ShellTool",
]
