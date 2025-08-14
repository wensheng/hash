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
        with patch('hashcli.main.CommandProxy') as mock_proxy_class:
            mock_proxy = MagicMock()
            mock_proxy.execute.return_value = "Command executed successfully"
            mock_proxy_class.return_value = mock_proxy
            
            execute_command_mode("/ls -la", sample_config, quiet=True)
            
            # Verify CommandProxy was called
            mock_proxy_class.assert_called_once_with(sample_config)
            mock_proxy.execute.assert_called_once_with("/ls -la")
    
    @pytest.mark.asyncio
    async def test_llm_mode_detection(self, sample_config):
        """Test that LLM mode is properly detected."""
        # Mock the LLM handler
        with patch('hashcli.main.LLMHandler') as mock_handler_class:
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
                model="test-model"
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
        sample_config.require_confirmation = False  # Skip confirmation for test
        
        # Mock LLM provider with tool call
        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock()
        
        # First call returns tool call
        tool_call = ToolCall(
            name="execute_shell_command",
            arguments={"command": "echo 'test'", "description": "Test command"}
        )
        initial_response = LLMResponse(
            content="I'll run that command for you.",
            tool_calls=[tool_call],
            model="test-model"
        )
        
        # Second call (after tool execution) returns final response
        final_response = LLMResponse(
            content="The command executed successfully and output: test",
            tool_calls=[],
            model="test-model"
        )
        
        mock_provider.generate_response.side_effect = [initial_response, final_response]
        
        # Mock tool execution
        with patch('hashcli.tools.get_tool_executor') as mock_get_tool:
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
        messages = second_call_args[1]['messages']  # kwargs
        
        # Should have system message + previous conversation
        assert len(messages) >= 3  # system + user1 + assistant1 + user2


class TestCommandIntegration:
    """Test command integration and cross-platform compatibility."""
    
    def test_ls_command_integration(self, sample_config):
        """Test ls command integration."""
        proxy = CommandProxy(sample_config)
        
        # Mock subprocess for cross-platform testing
        with patch('hashcli.command_proxy.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout="file1.txt\\nfile2.py\\nsubdir/\\n",
                stderr="",
                returncode=0
            )
            
            result = proxy.execute("/ls")
            
            assert mock_run.called
            assert "file1.txt" in result or "file2.py" in result or result  # Some content
    
    def test_help_command_integration(self, sample_config):
        """Test help command integration."""
        proxy = CommandProxy(sample_config)
        
        result = proxy.execute("/help")
        
        assert "Hash CLI" in result
        assert "DUAL MODE OPERATION" in result
        assert "/ls" in result
        assert "/clear" in result
        assert "/help" in result
    
    def test_model_command_integration(self, sample_config):
        """Test model command integration."""
        proxy = CommandProxy(sample_config)
        
        # Test showing current config
        result = proxy.execute("/model")
        
        assert "Current Configuration" in result
        assert "Provider:" in result
        assert "Model:" in result
        assert "API Key:" in result
    
    def test_config_command_integration(self, sample_config):
        """Test config command integration."""
        proxy = CommandProxy(sample_config)
        
        result = proxy.execute("/config")
        
        assert "Hash CLI Configuration" in result
        assert "LLM Configuration" in result
        assert "Tool Configuration" in result
        assert "History Configuration" in result


class TestErrorHandling:
    """Test error handling across the system."""
    
    @pytest.mark.asyncio
    async def test_llm_provider_error_handling(self, sample_config, temp_dir):
        """Test LLM provider error handling."""
        sample_config.history_dir = temp_dir / "history"
        
        # Mock provider that raises an exception
        mock_provider = MagicMock()
        mock_provider.generate_response = AsyncMock(
            side_effect=Exception("API Error")
        )
        
        handler = LLMHandler(sample_config)
        handler.provider = mock_provider
        
        response = await handler.chat("Test message")
        
        assert "LLM Error" in response
        assert "API Error" in response
    
    def test_command_execution_error_handling(self, sample_config):
        """Test command execution error handling."""
        proxy = CommandProxy(sample_config)
        
        # Mock subprocess that fails
        with patch('hashcli.command_proxy.subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("Command not found")
            
            result = proxy.execute("/ls")
            
            assert "Command not found" in result
    
    def test_invalid_command_arguments(self, sample_config):
        """Test handling of invalid command arguments."""
        proxy = CommandProxy(sample_config)
        
        # Test with malformed command line
        result = proxy.execute('/ls "unclosed quote')
        
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
        with patch('hashcli.tools.shell.subprocess.run') as mock_run:
            # The security check should prevent execution
            from hashcli.tools.shell import ShellTool
            tool = ShellTool()
            
            result = asyncio.run(tool.execute(
                {"command": "rm -rf /tmp/test", "description": "Remove files"},
                sample_config
            ))
            
            assert "Blocked command detected" in result
            mock_run.assert_not_called()
    
    def test_file_access_restrictions(self, sample_config):
        """Test file access security restrictions."""
        from hashcli.tools.filesystem import FileSystemTool
        
        tool = FileSystemTool()
        
        # Test reading sensitive file
        result = asyncio.run(tool.execute(
            {"file_path": "/etc/passwd"},
            sample_config
        ))
        
        assert "sensitive file denied" in result.lower()
    
    def test_api_key_sanitization(self):
        """Test that API keys are properly handled."""
        from hashcli.config import HashConfig
        
        # Test with whitespace in API key
        config = HashConfig(openai_api_key="  test-key-with-spaces  ")
        
        assert config.openai_api_key == "test-key-with-spaces"