"""Unit tests for the main CLI entry point."""

import pytest
from typer.testing import CliRunner

from hashcli.main import app, execute_command_mode, execute_llm_mode, display_result, handle_error
from hashcli.config import HashConfig, LLMProvider

runner = CliRunner()

def test_main_no_args_shows_welcome():
    """Test that running with no arguments shows the welcome message."""
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Hash CLI - Intelligent Terminal Assistant" in result.stdout

def test_version_callback():
    """Test the --version flag."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Hash CLI version" in result.stdout

def test_show_config_callback():
    """Test the --show-config flag."""
    result = runner.invoke(app, ["--show-config"])
    assert result.exit_code == 0
    assert "Hash CLI Configuration" in result.stdout

def test_setup_callback():
    """Test the --setup flag."""
    # We just test that it starts, not the interactive part
    result = runner.invoke(app, ["--setup"], input="1\nmy-api-key\n")
    assert result.exit_code == 0
    assert "Hash CLI Setup Wizard" in result.stdout

def test_command_mode_execution(mocker):
    """Test command proxy mode."""
    mock_proxy = mocker.patch("hashcli.main.CommandProxy")
    mock_instance = mock_proxy.return_value
    mock_instance.execute.return_value = "command output"

    config = HashConfig()
    execute_command_mode("/ls", config)
    mock_instance.execute.assert_called_with("/ls")

@pytest.mark.asyncio
async def test_llm_mode_execution(mocker):
    """Test LLM chat mode."""
    # We need to mock the async chat method
    mock_chat = mocker.AsyncMock(return_value="llm output")
    mock_handler = mocker.patch("hashcli.main.LLMHandler")
    mock_instance = mock_handler.return_value
    mock_instance.chat = mock_chat

    config = HashConfig()
    await execute_llm_mode("hello", config)
    mock_chat.assert_awaited_with("hello")

def test_display_result(capsys):
    """Test result display."""
    config = HashConfig(rich_output=True)
    display_result("test result", config)
    captured = capsys.readouterr()
    assert "Result" in captured.out
    assert "test result" in captured.out

    config = HashConfig(rich_output=False)
    display_result("plain result", config)
    captured = capsys.readouterr()
    assert "Result:" in captured.out
    assert "plain result" in captured.out

def test_handle_error(capsys):
    """Test error handling."""
    handle_error(ValueError("test error"), debug=False)
    captured = capsys.readouterr()
    assert "Error:" in captured.out
    assert "test error" in captured.out

    try:
        raise ValueError("debug error")
    except ValueError as e:
        handle_error(e, debug=True)

    captured = capsys.readouterr()
    assert "Debug Error Details" in captured.out
    assert "ValueError: debug error" in captured.out
