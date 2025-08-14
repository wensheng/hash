"""Clear command implementation for clearing conversation history."""

from typing import List

from ..command_proxy import Command
from ..config import HashConfig


class ClearCommand(Command):
    """Command to clear conversation history."""

    def execute(self, args: List[str], config: HashConfig) -> str:
        """Clear conversation history."""
        from ..history import ConversationHistory

        if not config.history_enabled:
            return "History is disabled in configuration."

        # Parse arguments
        clear_all = "--all" in args or "-a" in args

        try:
            history = ConversationHistory(config.history_dir)

            if clear_all:
                # Clear all history
                success = history.clear_all_history()
                if success:
                    return "All conversation history cleared successfully."
                else:
                    return "Failed to clear conversation history."
            else:
                # Clear old history (default: 30 days)
                days = 30

                # Check for custom days argument
                for i, arg in enumerate(args):
                    if arg == "--days" or arg == "-d":
                        if i + 1 < len(args):
                            try:
                                days = int(args[i + 1])
                            except ValueError:
                                return f"Invalid days value: {args[i + 1]}"
                        break

                cleared_count = history.clear_old_history(days)
                if cleared_count > 0:
                    return f"Cleared {cleared_count} old conversations (older than {days} days)."
                else:
                    return f"No conversations older than {days} days found."

        except Exception as e:
            if config.show_debug:
                import traceback

                return f"Error clearing history: {e}\n{traceback.format_exc()}"
            else:
                return f"Error clearing history: {e}"

    def get_help(self) -> str:
        """Get help text for the clear command."""
        return """Clear conversation history:
  /clear                    - Clear conversations older than 30 days
  /clear --days N          - Clear conversations older than N days
  /clear --all             - Clear ALL conversation history
  
Examples:
  /clear                   - Clear old conversations
  /clear --days 7          - Clear conversations older than 7 days
  /clear --all             - Clear everything (cannot be undone)"""
