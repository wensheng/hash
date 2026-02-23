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
            return self._show_command_help(args[0], config)
        else:
            # Show general help
            return self._show_general_help(config)

    def _show_general_help(self, config: HashConfig) -> str:
        """Show general help with all available commands."""
        # Import here to avoid circular imports
        from ..command_proxy import CommandProxy

        proxy = CommandProxy(config)
        available = proxy.get_available_commands()

        core_cmds = {"help", "history"}
        plugins = [cmd for cmd in available if cmd not in core_cmds]

        help_text = """Hash CLI - Command Mode

CORE COMMANDS:
  /help [command]             - Show help (this message)
  /history [options]          - Manage conversation history"""

        if plugins:
            help_text += "\n\nPLUGINS:"
            for cmd in plugins:
                help_text += f"\n  /{cmd:<26}"
        else:
            help_text += (
                "\n\nPLUGINS:\n"
                "  None installed. Install with `hashcli --add-cmd <path-to-plugin>`."
            )

        help_text += """

EXAMPLES:
  # /help history
  # /history list

For command-specific help: /help <command>"""

        return help_text

    def _show_command_help(self, command_name: str, config: HashConfig) -> str:
        """Show help for a specific command."""
        # Import here to avoid circular imports
        from ..command_proxy import CommandProxy

        proxy = CommandProxy(config)

        # Get help for the specific command
        command_help = proxy.get_command_help(command_name)

        if command_help:
            return f"Help for /{command_name}:\n\n{command_help}"
        else:
            available_commands = ", ".join(proxy.get_available_commands())
            return (
                f"Unknown command: /{command_name}\n\nAvailable commands:"
                f" {available_commands}\n\nUse '/help' for full help."
            )

    def get_help(self) -> str:
        """Get help text for the help command."""
        return """Show help information:
  /help                    - Show general help and all commands
  /help <command>          - Show help for specific command
  
Examples:
  /help                    - Show this help
  /help history            - Show help for history command"""
