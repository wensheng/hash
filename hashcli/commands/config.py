"""Config command implementation for configuration management."""

from typing import List

from ..command_proxy import Command
from ..config import HashConfig, save_config


class ConfigCommand(Command):
    """Command to show and manage configuration."""

    def execute(self, args: List[str], config: HashConfig) -> str:
        """Show or manage configuration."""

        if not args:
            return self._show_config(config)

        command = args[0].lower()

        if command == "show":
            return self._show_config(config)
        elif command == "save":
            return self._save_config(config)
        elif command == "stats":
            return self._show_stats(config)
        else:
            return f"Unknown config command: {command}\n{self.get_help()}"

    def _show_config(self, config: HashConfig) -> str:
        """Show current configuration."""
        output = "Hash CLI Configuration:\n\n"

        # LLM Configuration
        output += "[bold blue]LLM Configuration:[/bold blue]\n"
        output += f"  Provider: {config.llm_provider.value}\n"
        output += f"  Model: {config.get_current_model()}\n"
        output += f"  API Key: {'✓ Set' if config.get_current_api_key() else '✗ Not set'}\n\n"

        # Tool Configuration
        output += "[bold blue]Tool Configuration:[/bold blue]\n"
        output += f"  Command execution: {'Enabled' if config.allow_command_execution else 'Disabled'}\n"
        output += f"  Confirmation required: {'Yes' if config.require_confirmation else 'No'}\n"
        output += f"  Command timeout: {config.command_timeout}s\n"
        output += (
            f"  Sandbox commands: {'Yes' if config.sandbox_commands else 'No'}\n\n"
        )

        # History Configuration
        output += "[bold blue]History Configuration:[/bold blue]\n"
        output += f"  History enabled: {'Yes' if config.history_enabled else 'No'}\n"
        if config.history_enabled:
            output += f"  History directory: {config.history_dir}\n"
            output += f"  Max history size: {config.max_history_size}\n"
            output += f"  Retention days: {config.history_retention_days}\n"
        output += "\n"

        # Output Configuration
        output += "[bold blue]Output Configuration:[/bold blue]\n"
        output += f"  Rich output: {'Yes' if config.rich_output else 'No'}\n"
        output += f"  Debug mode: {'Yes' if config.show_debug else 'No'}\n"
        output += f"  Log level: {config.log_level.value}\n\n"

        # Security Configuration
        output += "[bold blue]Security Configuration:[/bold blue]\n"
        if config.allowed_commands:
            output += f"  Allowed commands: {', '.join(config.allowed_commands)}\n"
        else:
            output += f"  Allowed commands: All (no whitelist)\n"
        output += f"  Blocked commands: {', '.join(config.blocked_commands)}\n"

        return output.strip()

    def _save_config(self, config: HashConfig) -> str:
        """Save current configuration to file."""
        try:
            success = save_config(config)
            if success:
                config_path = config.history_dir.parent / "config.toml"
                return f"Configuration saved to {config_path}"
            else:
                return "Failed to save configuration"
        except Exception as e:
            return f"Error saving configuration: {e}"

    def _show_stats(self, config: HashConfig) -> str:
        """Show usage statistics."""
        if not config.history_enabled:
            return "History is disabled - no statistics available."

        try:
            from ..history import ConversationHistory

            history = ConversationHistory(config.history_dir)
            stats = history.get_statistics()

            output = "Hash CLI Usage Statistics:\n\n"
            output += f"Total conversations: {stats['total_sessions']}\n"
            output += f"Total messages: {stats['total_messages']}\n"
            output += f"Recent conversations (7d): {stats['recent_sessions_7d']}\n"
            output += f"Recent messages (7d): {stats['recent_messages_7d']}\n"
            output += f"Database size: {stats['database_size_bytes'] / 1024:.1f} KB\\n"
            output += f"Database location: {stats['database_path']}\\n"

            if stats["total_sessions"] > 0:
                avg_messages = stats["total_messages"] / stats["total_sessions"]
                output += f"Average messages per conversation: {avg_messages:.1f}\\n"

            return output

        except Exception as e:
            return f"Error getting statistics: {e}"

    def get_help(self) -> str:
        """Get help text for the config command."""
        return """Show and manage configuration:
  /config                  - Show current configuration
  /config show             - Show current configuration (same as above)  
  /config save             - Save current config to file
  /config stats            - Show usage statistics
  
Examples:
  /config                  - View all settings
  /config save             - Save to ~/.hashcli/config.toml
  /config stats            - See usage statistics"""
