"""Hash (HAcker SHell) - Intelligent CLI system with dual-mode functionality.

This package provides a modern CLI that combines LLM conversational assistance
with command proxy functionality, operating in two distinct modes:

- LLM Chat Mode: Natural language queries for intelligent assistance
- Command Proxy Mode: Slash-prefixed commands for direct functionality

The system is designed for cross-platform compatibility and extensibility,
supporting multiple LLM providers and built-in command extensions.
"""

from .command_proxy import CommandProxy
from .config import HashConfig
from .history import ConversationHistory
from .llm_handler import LLMHandler
from .main import app

__version__ = "0.1.0"
__author__ = "Hash CLI Team"
__email__ = "team@hashcli.dev"

__all__ = [
    "app",
    "HashConfig",
    "LLMHandler",
    "CommandProxy",
    "ConversationHistory",
]
