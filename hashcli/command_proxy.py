"""Command proxy system for handling slash-prefixed commands."""

import shlex
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from .config import HashConfig
from .ui import console


class Command(ABC):
    """Abstract base class for all commands."""

    @abstractmethod
    def execute(self, args: List[str], config: HashConfig) -> str:
        """Execute the command with given arguments."""
        pass

    @abstractmethod
    def get_help(self) -> str:
        """Get help text for this command."""
        pass

    def validate_args(self, args: List[str]) -> bool:
        """Validate command arguments. Override if needed."""
        return True


class CommandProxy:
    """Main command proxy that routes slash commands to their handlers."""

    def __init__(self, config: HashConfig):
        self.config = config
        self.commands = self._register_commands()

    def execute(self, command_line: str) -> str:
        """Execute a slash command."""
        # Remove leading slash and parse command
        command_line = command_line.lstrip().lstrip("/")

        if not command_line:
            return "No command specified. Use /help for available commands."

        # Parse command and arguments safely
        try:
            parts = shlex.split(command_line)
        except ValueError as e:
            return f"Error parsing command: {e}"

        if not parts:
            return "No command specified. Use /help for available commands."

        cmd = parts[0]
        args = parts[1:] if len(parts) > 1 else []

        # Check if command exists
        if cmd not in self.commands:
            return f"Unknown command: /{cmd}\nUse /help for available commands."

        # Get command handler
        handler = self.commands[cmd]

        # Validate arguments
        if not handler.validate_args(args):
            return f"Invalid arguments for /{cmd}\n{handler.get_help()}"

        # Execute command
        try:
            return handler.execute(args, self.config)
        except Exception as e:
            if self.config.show_debug:
                import traceback

                return f"Command execution error: {e}\n{traceback.format_exc()}"
            else:
                return f"Command execution error: {e}"

    def _register_commands(self) -> Dict[str, Command]:
        """Register all available commands."""
        from .commands import (
            ClearCommand,
            ConfigCommand,
            FixCommand,
            HelpCommand,
            ModelCommand,
            TLDRCommand,
        )

        return {
            "clean": ClearCommand(),
            "model": ModelCommand(),
            "fix": FixCommand(),
            "help": HelpCommand(),
            "config": ConfigCommand(),
            "tldr": TLDRCommand(),
            "history": HistoryCommand(),
            "exit": ExitCommand(),
            "quit": ExitCommand(),
        }

    def get_available_commands(self) -> List[str]:
        """Get list of available command names."""
        return sorted(self.commands.keys())

    def get_command_help(self, command: str) -> Optional[str]:
        """Get help for a specific command."""
        if command in self.commands:
            return self.commands[command].get_help()
        return None


# History command for conversation history management
class HistoryCommand(Command):
    """Command to manage conversation history."""

    def execute(self, args: List[str], config: HashConfig) -> str:
        from .history import ConversationHistory

        if not config.history_enabled:
            return "History is disabled in configuration."

        history = ConversationHistory(config.history_dir)

        if not args or args[0] == "list":
            # List recent conversations
            sessions = history.list_sessions()
            if not sessions:
                return "No conversation history found."

            output = "Recent conversations:\n"
            for session in sessions[-10:]:  # Show last 10
                output += f"  {session['id']}: {session['created']} ({session['message_count']} messages)\n"
            return output.strip()

        elif args[0] == "show" and len(args) > 1:
            # Show specific conversation
            session_id = args[1]
            messages = history.get_session_messages(session_id)
            if not messages:
                return f"No messages found for session {session_id}"

            output = f"Conversation {session_id}:\n\n"
            for msg in messages:
                role = msg["role"].upper()
                content = msg["content"][:200] + "..." if len(msg["content"]) > 200 else msg["content"]
                output += f"[{role}] {content}\n\n"
            return output.strip()

        elif args[0] == "clear":
            # Clear all history
            if history.clear_all_history():
                return "All conversation history cleared."
            else:
                return "Failed to clear history."

        else:
            return self.get_help()

    def get_help(self) -> str:
        return """Manage conversation history:
  /history list        - List recent conversations
  /history show <id>   - Show specific conversation
  /history clear       - Clear all history"""


# Exit command
class ExitCommand(Command):
    """Command to exit the application."""

    def execute(self, args: List[str], config: HashConfig) -> str:
        import sys

        console.print("[yellow]Goodbye![/yellow]")
        sys.exit(0)

    def get_help(self) -> str:
        return "Exit the Hash CLI application."
