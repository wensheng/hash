"""Unit tests for the main CLI entry point."""

import re

import pytest
from typer.testing import CliRunner

from hashcli.main import (
    _build_query_execution_policy,
    _extract_suggested_command,
    _is_command_oriented_query,
    _normalize_shell_input,
    _strip_execute_prompt_lines,
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
    execute_command_mode("/history list", config)
    mock_instance.execute.assert_called_with("/history list")


def test_add_cmd_installs_valid_plugin_file(mocker, tmp_path):
    """--add-cmd should validate and install a plugin file."""
    plugin_file = tmp_path / "hello.py"
    plugin_file.write_text(
        "\n".join(
            [
                "from typing import List",
                "from hashcli.command_proxy import Command",
                "",
                "class HelloCommand(Command):",
                "    def execute(self, args: List[str]) -> str:",
                "        return 'hello world'",
                "",
                "    def get_help(self) -> str:",
                "        return 'say hello'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    mock_home = tmp_path / "home"
    mock_home.mkdir()
    mocker.patch("hashcli.command_proxy.Path.home", return_value=mock_home)

    result = runner.invoke(app, ["--add-cmd", str(plugin_file)])

    installed_file = mock_home / ".hashcli" / "plugins" / "hello.py"
    assert result.exit_code == 0
    assert installed_file.exists()
    assert "Available slash command" in result.stdout
    assert "/hello" in result.stdout

    from hashcli.command_proxy import CommandProxy

    proxy = CommandProxy(HashConfig())
    assert "hello" in proxy.get_available_commands()
    assert proxy.execute("/hello") == "hello world"


def test_add_cmd_accepts_directory_with_single_plugin_file(mocker, tmp_path):
    """--add-cmd should accept a directory containing one plugin file."""
    plugin_dir = tmp_path / "plugin_dir"
    plugin_dir.mkdir()
    plugin_file = plugin_dir / "goodbye.py"
    plugin_file.write_text(
        "\n".join(
            [
                "from typing import List",
                "from hashcli.command_proxy import Command",
                "from hashcli.config import HashConfig",
                "",
                "class GoodbyeCommand(Command):",
                "    def execute(self, args: List[str], config: HashConfig) -> str:",
                "        return 'bye'",
                "",
                "    def get_help(self) -> str:",
                "        return 'say bye'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    mock_home = tmp_path / "home"
    mock_home.mkdir()
    mocker.patch("hashcli.command_proxy.Path.home", return_value=mock_home)

    result = runner.invoke(app, ["--add-cmd", str(plugin_dir)])

    installed_file = mock_home / ".hashcli" / "plugins" / "goodbye.py"
    assert result.exit_code == 0
    assert installed_file.exists()
    assert "/goodbye" in result.stdout


def test_add_cmd_rejects_plugin_without_command_subclass(mocker, tmp_path):
    """--add-cmd should fail if plugin file has no Command subclass."""
    bad_plugin = tmp_path / "bad.py"
    bad_plugin.write_text(
        "\n".join(
            [
                "class NotACommand:",
                "    pass",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    mock_home = tmp_path / "home"
    mock_home.mkdir()
    mocker.patch("hashcli.command_proxy.Path.home", return_value=mock_home)

    result = runner.invoke(app, ["--add-cmd", str(bad_plugin)])

    assert result.exit_code == 1
    assert "No Command subclass found" in result.stdout


def test_add_cmd_installs_repo_plugins_config_command(mocker, tmp_path):
    """Plugin templates in root plugins/ should install via --add-cmd."""
    mock_home = tmp_path / "home"
    mock_home.mkdir()
    mocker.patch("hashcli.command_proxy.Path.home", return_value=mock_home)

    result = runner.invoke(app, ["--add-cmd", "plugins/hello.py"])

    installed_file = mock_home / ".hashcli" / "plugins" / "hello.py"
    assert result.exit_code == 0
    assert installed_file.exists()
    assert "/hello" in result.stdout

    from hashcli.command_proxy import CommandProxy

    proxy = CommandProxy(HashConfig())
    assert "hello" in proxy.get_available_commands()
    assert "Hello from" in proxy.execute("/hello")


def test_normalize_shell_input_leading_hash_query():
    """Leading # should be stripped for query mode."""
    assert _normalize_shell_input("# how do I list files?") == "how do I list files?"


def test_normalize_shell_input_leading_hash_slash_command():
    """Leading # should preserve slash command routing."""
    assert _normalize_shell_input("  # /ls -la") == "/ls -la"


def test_normalize_shell_input_enhanced_hint():
    """Embedded # should become a command-hint query."""
    normalized = _normalize_shell_input("find # all node_modules folders")
    assert normalized.startswith("Task: all node_modules folders.")
    assert "Use `find` as command hint." in normalized
    assert "Execute a single concrete shell command now" in normalized


def test_build_query_execution_policy_how_to_forces_confirmation():
    """How-to queries should force confirmation regardless of config."""
    normalized = _normalize_shell_input("# how to find all __pycache__ in current dir")
    policy = _build_query_execution_policy(
        "# how to find all __pycache__ in current dir",
        normalized,
        require_confirmation=False,
    )
    assert policy.force_tool_confirmation is True
    assert policy.suggested_command_confirmation is True
    assert policy.force_command_oriented is True


def test_build_query_execution_policy_action_query_respects_config():
    """Action query without how-to should follow config confirmation."""
    normalized = _normalize_shell_input("# find all __pycache__ in current dir")
    policy_false = _build_query_execution_policy(
        "# find all __pycache__ in current dir",
        normalized,
        require_confirmation=False,
    )
    policy_true = _build_query_execution_policy(
        "# find all __pycache__ in current dir",
        normalized,
        require_confirmation=True,
    )
    assert policy_false.force_tool_confirmation is None
    assert policy_false.suggested_command_confirmation is False
    assert policy_true.force_tool_confirmation is None
    assert policy_true.suggested_command_confirmation is True


def test_build_query_execution_policy_command_hint_disables_confirmation():
    """Embedded command-hint mode should execute without confirmation prompts."""
    normalized = _normalize_shell_input("find # all __pycache__ folders")
    policy = _build_query_execution_policy(
        "find # all __pycache__ folders",
        normalized,
        require_confirmation=True,
    )
    assert policy.force_tool_confirmation is False
    assert policy.suggested_command_confirmation is False
    assert policy.force_command_oriented is True


def test_extract_suggested_command_ignores_general_knowledge_prose():
    """Natural-language answers should not trigger command execution prompts."""
    response = "More sensitive to blue wavelengths makes the sky appear blue."
    assert _extract_suggested_command(response, user_query="why is the sky blue") is None


def test_extract_suggested_command_from_explicit_confirmation_line():
    """Explicit command confirmation format should still be parsed."""
    response = "Use this command.\ndo you want execute `ls -la`?"
    assert _extract_suggested_command(response, user_query="how do I list files") == "ls -la"


def test_extract_suggested_command_prefers_latest_explicit_execute_line():
    """When multiple execute lines exist, prefer the latest explicit recommendation."""
    response = (
        "do you want execute `echo {5..20}`?\n"
        "Would you like me to execute `seq 5 20` to show you the output?"
    )
    assert _extract_suggested_command(response, user_query="how to print natural number from 5 to 20") == "seq 5 20"


def test_strip_execute_prompt_lines_removes_confirmation_text():
    """Displayed output should not include model-emitted execute prompts."""
    response = "Sky blue = Rayleigh scattering.\ndo you want execute `echo hi`?"
    assert _strip_execute_prompt_lines(response) == "Sky blue = Rayleigh scattering."


def test_strip_execute_prompt_lines_removes_would_you_like_variant():
    """Alternate execute prompt phrasing should also be removed from display."""
    response = "Answer text.\nWould you like me to execute `seq 5 20` to show you the output?"
    assert _strip_execute_prompt_lines(response) == "Answer text."


def test_is_command_oriented_query_general_question_is_false():
    """General knowledge should not trigger execution suggestions."""
    assert _is_command_oriented_query("why is the sky blue") is False


def test_is_command_oriented_query_how_to_is_true():
    """How-to command questions can still trigger execution suggestions."""
    assert _is_command_oriented_query("how do I list files in bash") is True


def test_main_routes_hash_prefixed_slash_to_command_mode(mocker):
    """# /... input should route to command mode after normalization."""
    config = HashConfig()
    mocker.patch("hashcli.main.load_configuration", return_value=config)
    mocker.patch("hashcli.main.validate_api_setup")
    command_mode = mocker.patch("hashcli.main.execute_command_mode")
    llm_mode = mocker.patch("hashcli.main.asyncio.run")

    result = runner.invoke(app, ["# /help"])

    assert result.exit_code == 0
    command_mode.assert_called_once_with("/help", config, False)
    llm_mode.assert_not_called()


def test_main_routes_embedded_hash_to_enhanced_llm_mode(mocker):
    """command # intent should route to LLM mode with command hint context."""
    config = HashConfig()
    mocker.patch("hashcli.main.load_configuration", return_value=config)
    mocker.patch("hashcli.main.validate_api_setup")
    command_mode = mocker.patch("hashcli.main.execute_command_mode")
    llm_mode = mocker.Mock(return_value=None)
    mocker.patch("hashcli.main.execute_llm_mode", llm_mode)
    mocker.patch("hashcli.main.asyncio.run")

    result = runner.invoke(app, ["find # all node_modules folders"])

    assert result.exit_code == 0
    command_mode.assert_not_called()
    llm_mode.assert_called_once()
    normalized_query = llm_mode.call_args.args[0]
    assert normalized_query.startswith("Task: all node_modules folders.")
    call_kwargs = llm_mode.call_args.kwargs
    assert call_kwargs["force_tool_confirmation"] is False
    assert call_kwargs["suggested_command_confirmation"] is False
    assert call_kwargs["force_command_oriented"] is True


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


@pytest.mark.asyncio
async def test_llm_mode_general_query_skips_execute_prompt(mocker):
    """General questions should not trigger execute confirmation prompts."""
    mock_chat = mocker.AsyncMock(return_value="Sky blue = Rayleigh scattering.\ndo you want execute `echo hi`?")
    mock_handler = mocker.patch("hashcli.main.LLMHandler")
    mock_instance = mock_handler.return_value
    mock_instance.chat = mock_chat
    mock_instance.last_tool_calls_executed = False

    mock_display = mocker.patch("hashcli.main.display_result")
    mock_maybe_execute = mocker.AsyncMock()
    mocker.patch("hashcli.main._maybe_execute_suggested_command", mock_maybe_execute)

    config = HashConfig()
    await execute_llm_mode("why is the sky blue", config)

    mock_display.assert_called_once()
    assert "do you want execute" not in mock_display.call_args.args[0].lower()
    mock_maybe_execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_llm_mode_command_query_can_offer_execute_prompt(mocker):
    """Shell-oriented how-to queries can still offer execution."""
    mock_chat = mocker.AsyncMock(return_value="do you want execute `ls -la`?")
    mock_handler = mocker.patch("hashcli.main.LLMHandler")
    mock_instance = mock_handler.return_value
    mock_instance.chat = mock_chat
    mock_instance.last_tool_calls_executed = False

    mock_maybe_execute = mocker.AsyncMock()
    mocker.patch("hashcli.main._maybe_execute_suggested_command", mock_maybe_execute)

    config = HashConfig()
    await execute_llm_mode("how do I list files", config)

    mock_maybe_execute.assert_awaited_once()
    assert mock_maybe_execute.call_args.kwargs["require_confirmation"] is True


@pytest.mark.asyncio
async def test_llm_mode_command_query_can_auto_execute_suggestion(mocker):
    """When confirmation is disabled for suggestions, no prompt is required."""
    mock_chat = mocker.AsyncMock(return_value="do you want execute `ls -la`?")
    mock_handler = mocker.patch("hashcli.main.LLMHandler")
    mock_instance = mock_handler.return_value
    mock_instance.chat = mock_chat
    mock_instance.last_tool_calls_executed = False

    mock_maybe_execute = mocker.AsyncMock()
    mocker.patch("hashcli.main._maybe_execute_suggested_command", mock_maybe_execute)

    config = HashConfig()
    await execute_llm_mode(
        "find all __pycache__ in current dir",
        config,
        suggested_command_confirmation=False,
        force_command_oriented=True,
    )

    mock_maybe_execute.assert_awaited_once()
    assert mock_maybe_execute.call_args.kwargs["require_confirmation"] is False


@pytest.mark.asyncio
async def test_llm_mode_passes_force_tool_confirmation_to_handler(mocker):
    """Per-query policy should be passed through to tool-call execution layer."""
    mock_chat = mocker.AsyncMock(return_value="done")
    mock_handler = mocker.patch("hashcli.main.LLMHandler")
    mock_instance = mock_handler.return_value
    mock_instance.chat = mock_chat
    mock_instance.last_tool_calls_executed = True

    config = HashConfig()
    await execute_llm_mode(
        "how to find all __pycache__ in current dir",
        config,
        force_tool_confirmation=True,
    )

    mock_chat.assert_awaited_once_with(
        "how to find all __pycache__ in current dir",
        force_tool_confirmation=True,
    )


@pytest.mark.asyncio
async def test_llm_mode_streaming_general_query_filters_execute_line(mocker):
    """Streaming output should hide execute prompt text for general queries."""

    async def _fake_chat(_input_text, stream_handler=None):
        stream_handler("Sky blue = Rayleigh scattering.\n")
        stream_handler("do you want execute `echo hi`?\n")
        return "Sky blue = Rayleigh scattering.\ndo you want execute `echo hi`?"

    mock_handler = mocker.patch("hashcli.main.LLMHandler")
    mock_instance = mock_handler.return_value
    mock_instance.chat = mocker.AsyncMock(side_effect=_fake_chat)
    mock_instance.last_tool_calls_executed = False

    mock_console_print = mocker.patch("hashcli.main.console.print")
    mock_maybe_execute = mocker.AsyncMock()
    mocker.patch("hashcli.main._maybe_execute_suggested_command", mock_maybe_execute)

    config = HashConfig(streaming=True)
    await execute_llm_mode("why is the sky blue", config, quiet=True)

    printed = " ".join(str(call.args[0]) for call in mock_console_print.call_args_list if call.args)
    assert "do you want execute" not in printed.lower()
    mock_maybe_execute.assert_not_awaited()


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
