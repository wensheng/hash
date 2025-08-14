"""Unit tests for command proxy system."""

import pytest
from unittest.mock import patch, MagicMock

from hashcli.command_proxy import CommandProxy, Command
from hashcli.commands.ls import LSCommand
from hashcli.commands.clear import ClearCommand
from hashcli.commands.help import HelpCommand


class TestCommandProxy:
    """Test the CommandProxy class."""
    
    def test_command_registration(self, sample_config):
        """Test that commands are properly registered."""
        proxy = CommandProxy(sample_config)
        
        available_commands = proxy.get_available_commands()
        
        expected_commands = ['ls', 'dir', 'clear', 'model', 'fix', 'help', 'config', 'history', 'exit', 'quit']
        for cmd in expected_commands:
            assert cmd in available_commands
    
    def test_command_parsing(self, sample_config):
        """Test command line parsing."""
        proxy = CommandProxy(sample_config)
        
        # Test simple command
        with patch.object(proxy.commands['help'], 'execute', return_value="Help text"):
            result = proxy.execute("/help")
            assert "Help text" in result
    
    def test_command_with_arguments(self, sample_config):
        """Test command with arguments."""
        proxy = CommandProxy(sample_config)
        
        # Mock the ls command
        with patch.object(proxy.commands['ls'], 'execute', return_value="Directory listing"):
            result = proxy.execute("/ls -la /home")
            assert "Directory listing" in result
    
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
        with patch.object(proxy.commands['help'], 'execute', side_effect=Exception("Test error")):
            result = proxy.execute("/help")
            assert "Command execution error" in result
    
    def test_get_command_help(self, sample_config):
        """Test getting help for specific commands."""
        proxy = CommandProxy(sample_config)
        
        help_text = proxy.get_command_help('help')
        assert help_text is not None
        assert "help" in help_text.lower()
        
        # Test unknown command
        unknown_help = proxy.get_command_help('nonexistent')
        assert unknown_help is None


class TestLSCommand:
    """Test the LS command."""
    
    def test_ls_command_basic(self, sample_config):
        """Test basic ls command execution."""
        ls_cmd = LSCommand()
        
        # Mock subprocess.run
        with patch('hashcli.command_proxy.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout="file1.txt\\nfile2.py\\n",
                stderr="",
                returncode=0
            )
            
            result = ls_cmd.execute([], sample_config)
            assert mock_run.called
    
    def test_ls_command_with_args(self, sample_config):
        """Test ls command with arguments."""
        ls_cmd = LSCommand()
        
        with patch('hashcli.command_proxy.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout="total 8\\n-rw-r--r-- 1 user user 100 Jan 1 12:00 file.txt\\n",
                stderr="",
                returncode=0
            )
            
            result = ls_cmd.execute(["-l"], sample_config)
            assert mock_run.called
    
    def test_ls_command_error_handling(self, sample_config):
        """Test ls command error handling."""
        ls_cmd = LSCommand()
        
        with patch('hashcli.command_proxy.subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("Command not found")
            
            result = ls_cmd.execute([], sample_config)
            assert "Command not found" in result
    
    def test_ls_help(self):
        """Test ls command help."""
        ls_cmd = LSCommand()
        help_text = ls_cmd.get_help()
        
        assert "List directory contents" in help_text
        assert "examples" in help_text.lower() or "example" in help_text.lower()


class TestClearCommand:
    """Test the Clear command."""
    
    def test_clear_command_history_disabled(self):
        """Test clear command when history is disabled."""
        from hashcli.config import HashConfig
        config = HashConfig(history_enabled=False)
        
        clear_cmd = ClearCommand()
        result = clear_cmd.execute([], config)
        
        assert "History is disabled" in result
    
    def test_clear_command_basic(self, sample_config, temp_dir):
        """Test basic clear command."""
        # Set up temporary history directory
        sample_config.history_dir = temp_dir / "history"
        sample_config.history_dir.mkdir(exist_ok=True)
        
        clear_cmd = ClearCommand()
        
        # Mock history operations
        with patch('hashcli.history.ConversationHistory') as mock_history_class:
            mock_history = MagicMock()
            mock_history.clear_old_history.return_value = 5
            mock_history_class.return_value = mock_history
            
            result = clear_cmd.execute([], sample_config)
            assert "Cleared 5 old conversations" in result
    
    def test_clear_command_all_history(self, sample_config, temp_dir):
        """Test clearing all history."""
        sample_config.history_dir = temp_dir / "history"
        sample_config.history_dir.mkdir(exist_ok=True)
        
        clear_cmd = ClearCommand()
        
        with patch('hashcli.history.ConversationHistory') as mock_history_class:
            mock_history = MagicMock()
            mock_history.clear_all_history.return_value = True
            mock_history_class.return_value = mock_history
            
            result = clear_cmd.execute(["--all"], sample_config)
            assert "All conversation history cleared" in result
    
    def test_clear_help(self):
        """Test clear command help."""
        clear_cmd = ClearCommand()
        help_text = clear_cmd.get_help()
        
        assert "Clear conversation history" in help_text
        assert "--all" in help_text
        assert "--days" in help_text


class TestHelpCommand:
    """Test the Help command."""
    
    def test_help_general(self, sample_config):
        """Test general help command."""
        help_cmd = HelpCommand()
        result = help_cmd.execute([], sample_config)
        
        assert "Hash CLI" in result
        assert "DUAL MODE OPERATION" in result
        assert "/ls" in result
        assert "/help" in result
    
    def test_help_specific_command(self, sample_config):
        """Test help for specific command."""
        help_cmd = HelpCommand()
        result = help_cmd.execute(["ls"], sample_config)
        
        assert "Help for /ls" in result
    
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