"""Unit tests for the main CLI entry point."""

import re

import pytest
from typer.testing import CliRunner

from hashcli.main import (
    app,
    execute_command_mode,
    execute_llm_mode,
    display_result,
    handle_error,
)
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


def test_setup_callback(mocker, tmp_path):
    """Test the --setup flag."""
    # Create a mock shell directory structure
    mock_shell_dir = tmp_path / "shell" / "zsh"
    mock_shell_dir.mkdir(parents=True)
    install_script = mock_shell_dir / "install.sh"
    install_script.write_text("#!/bin/bash\necho 'test'\n")
    install_script.chmod(0o755)

    # Mock Path.home() to use tmp_path
    mock_home = mocker.patch("hashcli.main.Path.home")
    mock_home.return_value = tmp_path

    # Mock the package shell directory location
    mock_resolve = mocker.patch("hashcli.main.Path.resolve")
    mock_package_path = tmp_path / "package"
    mock_package_path.mkdir()
    (mock_package_path / "shell").mkdir()
    mocker.patch("pathlib.Path.resolve", return_value=mock_package_path)

    # Copy our mock shell dir to the "package" location
    import shutil
    shutil.copytree(tmp_path / "shell", mock_package_path / "shell", dirs_exist_ok=True)

    mock_run = mocker.patch("hashcli.main.subprocess.run")

    result = runner.invoke(app, ["--setup"], env={"SHELL": "/bin/zsh"})
    assert result.exit_code == 0
    assert "Installing zsh shell integration" in result.stdout
    assert mock_run.called
    args, _kwargs = mock_run.call_args
    assert args[0][0] == "/bin/bash"
    assert "install.sh" in str(args[0][1])
    assert args[0][2] == "install"


def test_command_mode_execution(mocker):
    """Test command proxy mode."""
    mock_proxy = mocker.patch("hashcli.main.CommandProxy")
    mock_instance = mock_proxy.return_value
    mock_instance.execute.return_value = "command output"

    config = HashConfig()
    execute_command_mode("/clean", config)
    mock_instance.execute.assert_called_with("/clean")


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
    cleaned_output = re.sub(r"\x1b\[[0-9;]*m", "", captured.out)
    assert "ValueError: debug error" in cleaned_output
