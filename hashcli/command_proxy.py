"""Command proxy system for handling slash-prefixed commands."""

import importlib.util
import inspect
import shlex
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Type

from .config import HashConfig
from .ui import console


class Command(ABC):
    """Abstract base class for all commands."""

    @abstractmethod
    def execute(self, args: List[str], config: Optional[HashConfig] = None) -> str:
        """Execute the command with given arguments."""
        pass

    @abstractmethod
    def get_help(self) -> str:
        """Get help text for this command."""
        pass

    def validate_args(self, args: List[str]) -> bool:
        """Validate command arguments. Override if needed."""
        return True


def get_user_plugin_directory() -> Path:
    """Return the local plugin installation directory."""
    return Path.home() / ".hashcli" / "plugins"


def _expected_command_class_name(file_stem: str) -> str:
    parts = [part for part in file_stem.replace("-", "_").split("_") if part]
    return "".join(part.capitalize() for part in parts) + "Command"


def load_command_class_from_file(plugin_file: Path) -> Type[Command]:
    """Load and validate a Command subclass from a Python plugin file."""
    plugin_path = Path(plugin_file).expanduser().resolve()
    if not plugin_path.exists() or not plugin_path.is_file():
        raise ValueError(f"Plugin file not found: {plugin_path}")
    if plugin_path.suffix != ".py":
        raise ValueError(f"Plugin file must be a .py file: {plugin_path}")

    module_name = f"hashcli_user_plugin_{plugin_path.stem}_{abs(hash(str(plugin_path)))}"
    spec = importlib.util.spec_from_file_location(module_name, plugin_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Unable to load plugin module from: {plugin_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    command_classes: List[Type[Command]] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj is Command:
            continue
        if obj.__module__ != module.__name__:
            continue
        if issubclass(obj, Command):
            command_classes.append(obj)

    if not command_classes:
        raise ValueError(
            f"No Command subclass found in {plugin_path.name}. "
            "Define a class inheriting from hashcli.command_proxy.Command."
        )

    expected_class_name = _expected_command_class_name(plugin_path.stem)
    for command_class in command_classes:
        if command_class.__name__ == expected_class_name:
            return command_class

    if len(command_classes) == 1:
        return command_classes[0]

    class_names = ", ".join(sorted(command_class.__name__ for command_class in command_classes))
    raise ValueError(
        f"Multiple Command subclasses found in {plugin_path.name}: {class_names}. "
        f"Keep one subclass or add {expected_class_name}."
    )


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
            return self._execute_handler(handler, args)
        except Exception as e:
            if self.config.show_debug:
                import traceback

                return f"Command execution error: {e}\n{traceback.format_exc()}"
            else:
                return f"Command execution error: {e}"

    def _execute_handler(self, handler: Command, args: List[str]) -> str:
        """Execute handler while supporting legacy third-party signatures."""
        try:
            signature = inspect.signature(handler.execute)
        except (TypeError, ValueError):
            # Conservative fallback if inspection fails.
            return handler.execute(args, self.config)

        parameters = list(signature.parameters.values())
        positional_params = [
            param
            for param in parameters
            if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        has_var_positional = any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in parameters)
        keyword_only_config = next(
            (param for param in parameters if param.kind == inspect.Parameter.KEYWORD_ONLY and param.name == "config"),
            None,
        )

        if has_var_positional or len(positional_params) >= 2:
            return handler.execute(args, self.config)
        if keyword_only_config is not None:
            return handler.execute(args, config=self.config)
        return handler.execute(args)

    def _register_commands(self) -> Dict[str, Command]:
        """Register all available commands."""
        commands: Dict[str, Command] = {}

        # Register Core Commands (Always available)
        from .commands import HelpCommand

        commands["help"] = HelpCommand()
        commands["history"] = HistoryCommand()

        # Register installed plugins from ~/.hashcli/plugins
        plugin_dir = get_user_plugin_directory()
        if plugin_dir.exists():
            for plugin_file in sorted(plugin_dir.glob("*.py")):
                if plugin_file.name.startswith("_"):
                    continue

                command_name = plugin_file.stem.lower()
                if command_name in commands:
                    if self.config.show_debug:
                        console.print(
                            f"[yellow]Skipping plugin '{plugin_file.name}': /{command_name} already exists.[/yellow]"
                        )
                    continue

                try:
                    command_class = load_command_class_from_file(plugin_file)
                    commands[command_name] = command_class()
                except Exception as e:
                    if self.config.show_debug:
                        console.print(f"[red]Error loading local plugin '{plugin_file.name}': {e}[/red]")

        return commands

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
            requested_session_id = args[1]
            session_id = history.resolve_session_id(requested_session_id)
            if session_id is None:
                matches = history.find_session_ids(requested_session_id)
                if len(matches) > 1:
                    match_list = "\n".join(f"  {match}" for match in matches[:10])
                    return (
                        f"Ambiguous session ID prefix {requested_session_id!r}. "
                        "Matches:\n"
                        f"{match_list}"
                    )
                return f"No messages found for session {requested_session_id}"

            messages = history.get_session_messages(session_id)
            if not messages:
                return f"No messages found for session {requested_session_id}"

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
  /history show <id>   - Show specific conversation by full ID or unique prefix
  /history clear       - Clear all history"""
