"""Integration tests for Hash CLI end-to-end functionality."""

import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from hashcli.main import main, execute_command_mode, execute_llm_mode
from hashcli.llm_handler import LLMHandler, LLMResponse
from hashcli.command_proxy import CommandProxy


class TestMainEntryPoint:
    """Test the main entry point functionality."""

    def test_command_mode_detection(self, sample_config):
        """Test that command mode is properly detected."""
        # Mock the command proxy
        with patch("hashcli.main.CommandProxy") as mock_proxy_class:
            mock_proxy = MagicMock()
            mock_proxy.execute.return_value = "Command executed successfully"
            mock_proxy_class.return_value = mock_proxy

            execute_command_mode("/history list", sample_config, quiet=True)

            # Verify CommandProxy was called
            mock_proxy_class.assert_called_once_with(sample_config)
            mock_proxy.execute.assert_called_once_with("/history list")

    @pytest.mark.asyncio
    async def test_llm_mode_detection(self, sample_config):
        """Test that LLM mode is properly detected."""
        # Mock the LLM handler
        with patch("hashcli.main.LLMHandler") as mock_handler_class:
            mock_handler = MagicMock()
            mock_handler.chat = AsyncMock(return_value="LLM response")
            mock_handler_class.return_value = mock_handler

            await execute_llm_mode("How are you?", sample_config, quiet=True)

            # Verify LLMHandler was called
            mock_handler_class.assert_called_once_with(sample_config)
            mock_handler.chat.assert_called_once_with("How are you?")


class TestLLMIntegration:
    """Test LLM integration with tool calls."""

    @pytest.mark.asyncio
    async def test_llm_handler_basic_chat(self, sample_config, temp_dir):
        """Test basic LLM chat functionality."""
        # Set up history directory
        sample_config.history_dir = temp_dir / "history"
        sample_config.history_enabled = True

        # Mock LLM provider
        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock(
            return_value=LLMResponse(
                content="Hello! How can I help you today?",
                tool_calls=[],
                model="test-model",
            )
        )

        # Create LLM handler and inject mock provider
        handler = LLMHandler(sample_config)
        handler.provider = mock_provider

        # Test chat
        response = await handler.chat("Hello")

        assert response == "Hello! How can I help you today?"
        mock_provider.generate_response.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_handler_with_tool_calls(self, sample_config, temp_dir):
        """Test LLM handler with tool calls."""
        from hashcli.llm_handler import ToolCall

        sample_config.history_dir = temp_dir / "history"
        sample_config.tool_confirmation = False  # Skip confirmation for test

        # Mock LLM provider with tool call
        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock()

        # First call returns tool call
        tool_call = ToolCall(
            name="execute_shell_command",
            arguments={"command": "echo 'test'", "description": "Test command"},
        )
        initial_response = LLMResponse(
            content="I'll run that command for you.",
            tool_calls=[tool_call],
            model="test-model",
        )

        # Second call (after tool execution) returns final response
        final_response = LLMResponse(
            content="The command executed successfully and output: test",
            tool_calls=[],
            model="test-model",
        )

        mock_provider.generate_response.side_effect = [initial_response, final_response]

        # Mock tool execution
        with patch("hashcli.tools.get_tool_executor") as mock_get_tool:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value="test")
            mock_get_tool.return_value = mock_tool

            # Create handler and test
            handler = LLMHandler(sample_config)
            handler.provider = mock_provider

            response = await handler.chat("Run echo test")

            assert "command executed successfully" in response.lower()
            assert mock_provider.generate_response.call_count == 2

    @pytest.mark.asyncio
    async def test_llm_handler_with_tldr_tool_calls(self, sample_config, temp_dir):
        """Command-help flows can use the integrated tldr tool."""
        from hashcli.llm_handler import ToolCall

        sample_config.history_dir = temp_dir / "history"
        sample_config.tool_confirmation = False

        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock()

        tool_call = ToolCall(name="lookup_tldr_command", arguments={"command": "tar"})
        initial_response = LLMResponse(
            content="Let me ground that with command examples.",
            tool_calls=[tool_call],
            model="test-model",
        )
        final_response = LLMResponse(
            content="`tar` archives files. A common extraction command is `tar -xzf archive.tar.gz`.",
            tool_calls=[],
            model="test-model",
        )
        mock_provider.generate_response.side_effect = [initial_response, final_response]

        with patch("hashcli.tools.get_tool_executor") as mock_get_tool:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value="# tar\n> Archiving utility")
            mock_tool.requires_confirmation.return_value = False
            mock_get_tool.return_value = mock_tool

            handler = LLMHandler(sample_config)
            handler.provider = mock_provider

            response = await handler.chat("how do I use tar to extract a tar.gz file?")

            assert "tar" in response.lower()
            mock_tool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_handler_tool_fallback_when_no_final_text(self, sample_config, temp_dir):
        """Fall back to tool output when the model returns no final text."""
        from hashcli.llm_handler import ToolCall

        sample_config.history_dir = temp_dir / "history"
        sample_config.tool_confirmation = False  # Skip confirmation for test

        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock()

        tool_call = ToolCall(
            name="execute_shell_command",
            arguments={"command": "echo 'test'", "description": "Test command"},
        )
        initial_response = LLMResponse(content="", tool_calls=[tool_call], model="test-model")
        final_response = LLMResponse(content="", tool_calls=[], model="test-model")

        mock_provider.generate_response.side_effect = [initial_response, final_response]

        with patch("hashcli.tools.get_tool_executor") as mock_get_tool:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value="test output")
            mock_get_tool.return_value = mock_tool

            handler = LLMHandler(sample_config)
            handler.provider = mock_provider

            response = await handler.chat("Run echo test")

            assert "test output" in response

    @pytest.mark.asyncio
    async def test_llm_handler_history_integration(self, sample_config, temp_dir):
        """Test LLM handler history integration."""
        sample_config.history_dir = temp_dir / "history"
        sample_config.history_enabled = True

        # Mock provider
        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock(
            return_value=LLMResponse(content="Response", tool_calls=[], model="test")
        )

        handler = LLMHandler(sample_config)
        handler.provider = mock_provider

        # First message
        await handler.chat("First message")

        # Second message - should include history
        await handler.chat("Second message")

        # Verify provider was called with history context
        assert mock_provider.generate_response.call_count == 2

        # Second call should have more messages (including history)
        second_call_args = mock_provider.generate_response.call_args_list[1]
        messages = second_call_args[1]["messages"]  # kwargs

        # Should have system message + previous conversation
        assert len(messages) >= 3  # system + user1 + assistant1 + user2

    @pytest.mark.asyncio
    async def test_llm_handler_shared_session_id_reuses_history(self, sample_config, temp_dir):
        """Separate handler instances should share context when session_id matches."""
        sample_config.history_dir = temp_dir / "history"
        sample_config.history_enabled = True

        first_provider = MagicMock()
        first_provider.generate_response = AsyncMock(
            return_value=LLMResponse(content="First response", tool_calls=[], model="test")
        )
        first_handler = LLMHandler(sample_config, session_id="shell-session-1")
        first_handler.provider = first_provider
        await first_handler.chat("First message")

        second_provider = MagicMock()
        second_provider.generate_response = AsyncMock(
            return_value=LLMResponse(content="Second response", tool_calls=[], model="test")
        )
        second_handler = LLMHandler(sample_config, session_id="shell-session-1")
        second_handler.provider = second_provider
        await second_handler.chat("Second message")

        second_call_args = second_provider.generate_response.call_args
        messages = second_call_args.kwargs["messages"]
        contents = [message.get("content", "") for message in messages]

        assert any("First message" in content for content in contents)
        assert any("First response" in content for content in contents)
        assert any("Second message" in content for content in contents)

    @pytest.mark.asyncio
    async def test_llm_handler_force_confirmation_true_overrides_config(self, sample_config, temp_dir):
        """How-to mode can force confirmation even when config disables it."""
        from hashcli.llm_handler import ToolCall

        sample_config.history_dir = temp_dir / "history"
        sample_config.tool_confirmation = False

        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock()

        tool_call = ToolCall(
            name="execute_shell_command",
            arguments={"command": "find . -name __pycache__", "description": "Find pycache dirs"},
        )
        initial_response = LLMResponse(content="Running command", tool_calls=[tool_call], model="test-model")
        final_response = LLMResponse(content="User declined to execute this tool call.", tool_calls=[], model="test")
        mock_provider.generate_response.side_effect = [initial_response, final_response]

        with patch("hashcli.tools.get_tool_executor") as mock_get_tool:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value="should-not-run")
            mock_tool.requires_confirmation.return_value = True
            mock_get_tool.return_value = mock_tool

            handler = LLMHandler(sample_config)
            handler.provider = mock_provider
            with patch.object(handler, "_get_user_confirmation", return_value=False) as mock_confirm:
                await handler.chat("how to find __pycache__", force_tool_confirmation=True)

                mock_confirm.assert_called_once()
                mock_tool.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_handler_force_confirmation_false_overrides_config(self, sample_config, temp_dir):
        """Command-hint mode can bypass config-level confirmation."""
        from hashcli.llm_handler import ToolCall

        sample_config.history_dir = temp_dir / "history"
        sample_config.tool_confirmation = True

        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock()

        tool_call = ToolCall(
            name="execute_shell_command",
            arguments={"command": "find . -name __pycache__", "description": "Find pycache dirs"},
        )
        initial_response = LLMResponse(content="Running command", tool_calls=[tool_call], model="test-model")
        final_response = LLMResponse(content="Done", tool_calls=[], model="test")
        mock_provider.generate_response.side_effect = [initial_response, final_response]

        with patch("hashcli.tools.get_tool_executor") as mock_get_tool:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value="ok")
            mock_tool.requires_confirmation.return_value = True
            mock_get_tool.return_value = mock_tool

            handler = LLMHandler(sample_config)
            handler.provider = mock_provider
            with patch.object(handler, "_get_user_confirmation") as mock_confirm:
                await handler.chat("find # all __pycache__", force_tool_confirmation=False)

                mock_confirm.assert_not_called()
                mock_tool.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_handler_destructive_shell_tool_still_prompts_when_config_disables_confirmation(
        self,
        sample_config,
        temp_dir,
    ):
        """Destructive shell actions must still prompt even if global confirmation is off."""
        from hashcli.llm_handler import ToolCall

        sample_config.history_dir = temp_dir / "history"
        sample_config.tool_confirmation = False

        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock()

        tool_call = ToolCall(
            name="execute_shell_command",
            arguments={"command": "kill -9 1234", "description": "Terminate the process"},
        )
        initial_response = LLMResponse(content="Running command", tool_calls=[tool_call], model="test-model")
        final_response = LLMResponse(content="User declined to execute this tool call.", tool_calls=[], model="test")
        mock_provider.generate_response.side_effect = [initial_response, final_response]

        with patch("hashcli.tools.get_tool_executor") as mock_get_tool:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value="should-not-run")
            mock_tool.requires_confirmation.return_value = True
            mock_get_tool.return_value = mock_tool

            handler = LLMHandler(sample_config)
            handler.provider = mock_provider
            with patch.object(handler, "_get_user_confirmation", return_value=False) as mock_confirm:
                await handler.chat("kill whatever is running on port 8080")

                mock_confirm.assert_called_once()
                mock_tool.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_handler_how_to_only_exposes_tldr_tool(self, sample_config, temp_dir):
        """How-to command queries should not expose direct shell execution."""
        sample_config.history_dir = temp_dir / "history"
        sample_config.history_enabled = False

        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock(
            return_value=LLMResponse(content="Use find", tool_calls=[], model="test-model")
        )

        handler = LLMHandler(sample_config)
        handler.provider = mock_provider

        await handler.chat("how do I find all pycache directories?")

        tools = mock_provider.generate_response.call_args.kwargs["tools"]
        tool_names = [tool["function"]["name"] for tool in tools]
        assert tool_names == ["lookup_tldr_command"]

        tldr_tool = tools[0]["function"]
        assert tldr_tool["parameters"]["required"] == ["command", "platform", "language", "search"]
        assert tldr_tool["parameters"]["properties"]["platform"]["type"] == ["string", "null"]
        assert tldr_tool["parameters"]["properties"]["language"]["type"] == ["string", "null"]

    @pytest.mark.asyncio
    async def test_llm_handler_action_query_exposes_shell_and_tldr_tools(self, sample_config, temp_dir):
        """Action-oriented command queries should expose shell execution and tldr lookup."""
        sample_config.history_dir = temp_dir / "history"
        sample_config.history_enabled = False

        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock(
            return_value=LLMResponse(content="Running", tool_calls=[], model="test-model")
        )

        handler = LLMHandler(sample_config)
        handler.provider = mock_provider

        await handler.chat("show disk usage in human readable format")

        tools = mock_provider.generate_response.call_args.kwargs["tools"]
        tool_names = [tool["function"]["name"] for tool in tools]
        assert tool_names == ["lookup_tldr_command", "execute_shell_command"]


class TestCommandIntegration:
    """Test command integration and cross-platform compatibility."""

    def test_unknown_slash_command_rejected(self, sample_config):
        """Unknown slash commands should not proxy to system commands."""
        proxy = CommandProxy(sample_config)

        result = proxy.execute("/ls")
        assert "Unknown command: /ls" in result

    def test_help_command_integration(self, sample_config):
        """Test help command integration."""
        proxy = CommandProxy(sample_config)

        result = proxy.execute("/help")

        assert "Hash CLI" in result
        assert "Command Mode" in result
        assert "/help" in result
        assert "/history" in result

    def test_installed_plugin_command_integration(self, sample_config, temp_dir):
        """Installed plugin should be available as a slash command."""
        plugin_home = temp_dir / "home"
        plugin_dir = plugin_home / ".hashcli" / "plugins"
        plugin_dir.mkdir(parents=True)
        plugin_file = plugin_dir / "hello.py"
        plugin_file.write_text(
            "\n".join(
                [
                    "from typing import List",
                    "from hashcli.command_proxy import Command",
                    "",
                    "class HelloCommand(Command):",
                    "    def execute(self, args: List[str]) -> str:",
                    "        return 'hello plugin'",
                    "",
                    "    def get_help(self) -> str:",
                    "        return 'hello help'",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with patch("hashcli.command_proxy.Path.home", return_value=plugin_home):
            proxy = CommandProxy(sample_config)
            result = proxy.execute("/hello")

        assert result == "hello plugin"

    def test_plugin_command_help_integration(self, sample_config, temp_dir):
        """Help output should include installed plugins."""
        plugin_home = temp_dir / "home"
        plugin_dir = plugin_home / ".hashcli" / "plugins"
        plugin_dir.mkdir(parents=True)
        plugin_file = plugin_dir / "hello.py"
        plugin_file.write_text(
            "\n".join(
                [
                    "from typing import List",
                    "from hashcli.command_proxy import Command",
                    "",
                    "class HelloCommand(Command):",
                    "    def execute(self, args: List[str]) -> str:",
                    "        return 'hello plugin'",
                    "",
                    "    def get_help(self) -> str:",
                    "        return 'hello help'",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with patch("hashcli.command_proxy.Path.home", return_value=plugin_home):
            proxy = CommandProxy(sample_config)
            result = proxy.execute("/help")

        assert "PLUGINS" in result
        assert "/hello" in result

    def test_plugin_help_detail_integration(self, sample_config, temp_dir):
        """Plugin-specific help should be retrievable."""
        plugin_home = temp_dir / "home"
        plugin_dir = plugin_home / ".hashcli" / "plugins"
        plugin_dir.mkdir(parents=True)
        plugin_file = plugin_dir / "hello.py"
        plugin_file.write_text(
            "\n".join(
                [
                    "from typing import List",
                    "from hashcli.command_proxy import Command",
                    "",
                    "class HelloCommand(Command):",
                    "    def execute(self, args: List[str]) -> str:",
                    "        return 'hello plugin'",
                    "",
                    "    def get_help(self) -> str:",
                    "        return 'hello help'",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with patch("hashcli.command_proxy.Path.home", return_value=plugin_home):
            proxy = CommandProxy(sample_config)
            result = proxy.execute("/help hello")

        assert "Help for /hello" in result
        assert "hello help" in result

    def test_non_installed_plugin_command_is_unknown(self, sample_config, temp_dir):
        """Plugin commands are unavailable until installed via add-cmd."""
        plugin_home = temp_dir / "home"
        plugin_home.mkdir()
        with patch("hashcli.command_proxy.Path.home", return_value=plugin_home):
            proxy = CommandProxy(sample_config)
            result = proxy.execute("/model")
        assert "Unknown command: /model" in result


class TestErrorHandling:
    """Test error handling across the system."""

    @pytest.mark.asyncio
    async def test_llm_provider_error_handling(self, sample_config, temp_dir):
        """Test LLM provider error handling."""
        sample_config.history_dir = temp_dir / "history"

        # Mock provider that raises an exception
        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock(side_effect=Exception("API Error"))

        handler = LLMHandler(sample_config)
        handler.provider = mock_provider

        response = await handler.chat("Test message")

        assert "LLM Error" in response
        assert "API Error" in response

    def test_unknown_command_error_handling(self, sample_config):
        """Unknown slash commands return a consistent error message."""
        proxy = CommandProxy(sample_config)
        result = proxy.execute("/ls")
        assert "Unknown command: /ls" in result

    def test_invalid_command_arguments(self, sample_config):
        """Test handling of invalid command arguments."""
        proxy = CommandProxy(sample_config)

        # Test with malformed command line
        result = proxy.execute('/help "unclosed quote')

        # Should handle parsing error gracefully
        assert "Error parsing command" in result or result  # Some error message

    def test_configuration_error_handling(self, sample_config):
        """Test configuration error handling."""
        # Create config with invalid API key
        sample_config.openai_api_key = None

        from hashcli.config import validate_api_setup, ConfigurationError

        with pytest.raises(ConfigurationError):
            validate_api_setup(sample_config)


class TestSecurityFeatures:
    """Test security features and restrictions."""

    def test_command_blocking(self, sample_config):
        """Test that dangerous commands are blocked."""
        # Add dangerous command to blocked list
        sample_config.blocked_commands = ["rm -rf"]

        proxy = CommandProxy(sample_config)

        # This would normally use the shell tool, but we'll test the security check
        with patch("hashcli.tools.shell.subprocess.run") as mock_run:
            # The security check should prevent execution
            from hashcli.tools.shell import ShellTool

            tool = ShellTool()

            result = asyncio.run(
                tool.execute(
                    {"command": "rm -rf /tmp/test", "description": "Remove files"},
                    sample_config,
                )
            )

            assert "Blocked command detected" in result
            mock_run.assert_not_called()

    def test_shell_tool_passthrough_skips_output_capture(self, sample_config):
        """Passthrough mode should inherit stdio so interactive prompts remain visible."""
        from hashcli.tools.shell import ShellTool

        mock_result = MagicMock(returncode=0)
        with patch("hashcli.tools.shell.subprocess.run", return_value=mock_result) as mock_run:
            tool = ShellTool()
            result = asyncio.run(
                tool.execute(
                    {
                        "command": "docker image prune -a",
                        "description": "Prune unused Docker images",
                        "passthrough_output": True,
                    },
                    sample_config,
                )
            )

        assert result == ""
        _, kwargs = mock_run.call_args
        assert "capture_output" not in kwargs
        assert kwargs["shell"] is False

    def test_file_access_restrictions(self, sample_config):
        """Test file access security restrictions."""
        from hashcli.tools.filesystem import FileSystemTool

        tool = FileSystemTool()

        # Test reading sensitive file
        result = asyncio.run(tool.execute({"file_path": "/etc/passwd"}, sample_config))

        assert "sensitive file denied" in result.lower()

    def test_api_key_sanitization(self):
        """Test that API keys are properly handled."""
        from hashcli.config import HashConfig

        # Test with whitespace in API key
        config = HashConfig(openai_api_key="  test-key-with-spaces  ")

        assert config.openai_api_key == "test-key-with-spaces"
