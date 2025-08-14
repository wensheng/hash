"""Help command implementation for showing available commands."""

from typing import List

from ..command_proxy import Command
from ..config import HashConfig


class HelpCommand(Command):
    """Command to show help information."""

    def execute(self, args: List[str], config: HashConfig) -> str:
        """Show help information."""

        if args and args[0] != "":
            # Show help for specific command
            return self._show_command_help(args[0])
        else:
            # Show general help
            return self._show_general_help()

    def _show_general_help(self) -> str:
        """Show general help with all available commands."""
        help_text = """Hash CLI - Intelligent Terminal Assistant

DUAL MODE OPERATION:
  hashcli <natural language>  - LLM chat mode for questions & assistance
  hashcli /<command>          - Command proxy mode for direct actions

AVAILABLE COMMANDS:
  /ls [args]                  - List directory contents (cross-platform)
  /clear [options]            - Clear conversation history  
  /model [options]            - Switch LLM models and providers
  /fix <description>          - Get coding assistance
  /help [command]             - Show help (this message)
  /config                     - Show current configuration
  /history [options]          - Manage conversation history
  /exit, /quit                - Exit the application

EXAMPLES:
  # LLM Mode:
  hashcli how do I find large files?
  hashcli explain this error: permission denied
  hashcli help me optimize this Python script
  
  # Command Mode:
  hashcli /ls -la
  hashcli /model set gpt-5-mini
  hashcli /clear --days 7
  hashcli /fix my tests are failing

GETTING STARTED:
  1. Set API key: export OPENAI_API_KEY="your-key"
  2. Try: hashcli hello world
  3. Or: hashcli /help model

For command-specific help: /help <command>"""

        return help_text

    def _show_command_help(self, command_name: str) -> str:
        """Show help for a specific command."""
        # Import here to avoid circular imports
        from ..command_proxy import CommandProxy
        from ..config import HashConfig

        # Create a temporary config to access command registry
        temp_config = HashConfig()
        proxy = CommandProxy(temp_config)

        # Get help for the specific command
        command_help = proxy.get_command_help(command_name)

        if command_help:
            return f"Help for /{command_name}:\\n\\n{command_help}"
        else:
            available_commands = ", ".join(proxy.get_available_commands())
            return f"Unknown command: /{command_name}\\n\\nAvailable commands: {available_commands}\\n\\nUse '/help' for full help."

    def get_help(self) -> str:
        """Get help text for the help command."""
        return """Show help information:
  /help                    - Show general help and all commands
  /help <command>          - Show help for specific command
  
Examples:
  /help                    - Show this help
  /help model              - Show help for model command
  /help ls                 - Show help for ls command"""
