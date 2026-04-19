"""Unit tests for command proxy system."""

from unittest.mock import patch, MagicMock

from hashcli.command_proxy import Command, CommandProxy
from hashcli.history import ConversationHistory
from hashcli.commands.help import HelpCommand


class TestCommandProxy:
    """Test the CommandProxy class."""

    def test_command_registration(self, sample_config):
        """Test that commands are properly registered."""
        proxy = CommandProxy(sample_config)

        available_commands = proxy.get_available_commands()

        expected_commands = [
            "help",
            "history",
        ]
        for cmd in expected_commands:
            assert cmd in available_commands

    def test_no_builtin_plugins_without_installation(self, sample_config, temp_dir, mocker):
        """Only core slash commands are available when no plugins are installed."""
        empty_home = temp_dir / "home"
        empty_home.mkdir()
        mocker.patch("hashcli.command_proxy.Path.home", return_value=empty_home)

        proxy = CommandProxy(sample_config)
        available = proxy.get_available_commands()

        assert "help" in available
        assert "history" in available

    def test_command_parsing(self, sample_config):
        """Test command line parsing."""
        proxy = CommandProxy(sample_config)

        # Test simple command
        with patch.object(proxy.commands["help"], "execute", return_value="Help text"):
            result = proxy.execute("/help")
            assert "Help text" in result

    def test_command_with_arguments(self, sample_config):
        """Test command with arguments."""
        proxy = CommandProxy(sample_config)

        class EchoCommand(Command):
            def execute(self, args):
                return " ".join(args)

            def get_help(self):
                return "echo args"

        proxy.commands["echo"] = EchoCommand()
        result = proxy.execute("/echo hello world")
        assert result == "hello world"

    def test_unknown_command(self, sample_config):
        """Test handling of unknown commands."""
        proxy = CommandProxy(sample_config)

        result = proxy.execute("/unknown_command")
        assert "Unknown command" in result
        assert "/help" in result

    def test_empty_command(self, sample_config):
        """Test handling of empty commands."""
        proxy = CommandProxy(sample_config)

        result = proxy.execute("/")
        assert "No command specified" in result

    def test_command_execution_error(self, sample_config):
        """Test handling of command execution errors."""
        proxy = CommandProxy(sample_config)

        # Mock a command that raises an exception
        with patch.object(proxy.commands["help"], "execute", side_effect=Exception("Test error")):
            result = proxy.execute("/help")
            assert "Command execution error" in result

    def test_get_command_help(self, sample_config):
        """Test getting help for specific commands."""
        proxy = CommandProxy(sample_config)

        help_text = proxy.get_command_help("help")
        assert help_text is not None
        assert "help" in help_text.lower()

        # Test unknown command
        unknown_help = proxy.get_command_help("nonexistent")
        assert unknown_help is None

    def test_command_without_config_argument(self, sample_config):
        """Third-party commands may implement execute(self, args) only."""

        class HelloNoConfigCommand(Command):
            def execute(self, args):
                return "hello without config"

            def get_help(self):
                return "help"

        proxy = CommandProxy(sample_config)
        proxy.commands["hello"] = HelloNoConfigCommand()

        result = proxy.execute("/hello")
        assert result == "hello without config"

    def test_history_show_accepts_unique_session_prefix(self, sample_config, temp_dir):
        """History show should resolve a unique leading session ID prefix."""
        sample_config.history_dir = temp_dir / "history"
        history = ConversationHistory(sample_config.history_dir)
        session_id = "12345678-1234-1234-1234-123456789abc"
        history.start_session(title="Prefix Session", session_id=session_id)
        history.add_message(session_id, "user", "hello")

        proxy = CommandProxy(sample_config)
        result = proxy.execute("/history show 12345678")

        assert f"Conversation {session_id}:" in result
        assert "[USER] hello" in result

    def test_history_show_rejects_ambiguous_session_prefix(self, sample_config, temp_dir):
        """History show should ask for a longer prefix when multiple sessions match."""
        sample_config.history_dir = temp_dir / "history"
        history = ConversationHistory(sample_config.history_dir)
        first_id = "12345678-1234-1234-1234-123456789abc"
        second_id = "12345678-abcd-1234-1234-123456789abc"
        history.start_session(title="First Session", session_id=first_id)
        history.start_session(title="Second Session", session_id=second_id)

        proxy = CommandProxy(sample_config)
        result = proxy.execute("/history show 12345678")

        assert "Ambiguous session ID prefix" in result
        assert first_id in result
        assert second_id in result


class TestHelpCommand:
    """Test the Help command."""

    def test_help_general(self, sample_config):
        """Test general help command."""
        help_cmd = HelpCommand()
        result = help_cmd.execute([], sample_config)

        assert "Hash CLI" in result
        assert "Command Mode" in result
        assert "PLUGINS" in result
        assert "/help" in result

    def test_help_shows_installed_plugin(self, sample_config, temp_dir, mocker):
        """General help should list installed plugins."""
        plugin_home = temp_dir / "home"
        plugin_dir = plugin_home / ".hashcli" / "plugins"
        plugin_dir.mkdir(parents=True)
        plugin_file = plugin_dir / "hello.py"
        plugin_file.write_text(
            "\n".join([
                "from typing import List",
                "from hashcli.command_proxy import Command",
                "from hashcli.config import HashConfig",
                "",
                "class HelloCommand(Command):",
                "    def execute(self, args: List[str], config: HashConfig) -> str:",
                "        return 'hello'",
                "",
                "    def get_help(self) -> str:",
                "        return 'hello help'",
            ])
            + "\n",
            encoding="utf-8",
        )

        mocker.patch("hashcli.command_proxy.Path.home", return_value=plugin_home)

        help_cmd = HelpCommand()
        result = help_cmd.execute([], sample_config)

        assert "PLUGINS" in result
        assert "/hello" in result

    def test_help_specific_command(self, sample_config):
        """Test help for specific command."""
        help_cmd = HelpCommand()
        result = help_cmd.execute(["history"], sample_config)

        assert "Help for /history" in result

    def test_help_unknown_command(self, sample_config):
        """Test help for unknown command."""
        help_cmd = HelpCommand()
        result = help_cmd.execute(["nonexistent"], sample_config)

        assert "Unknown command" in result
        assert "Available commands" in result

    def test_help_help(self):
        """Test help command's own help."""
        help_cmd = HelpCommand()
        help_text = help_cmd.get_help()

        assert "Show help information" in help_text
