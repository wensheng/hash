"""Unit tests for the main CLI entry point."""

import hashlib
import re
import uuid

import pytest
from typer.testing import CliRunner

from hashcli.main import (
    _build_query_execution_policy,
    _build_shell_scope_fingerprint,
    _extract_suggested_command,
    _is_command_oriented_query,
    _maybe_execute_suggested_command,
    _normalize_shell_input,
    _resolve_provider_option,
    _resolve_conversation_session_id,
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
    assert "Hash CLI - Command Assistant" in result.stdout


def test_version_callback():
    """Test the --version flag."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Hash CLI version" in result.stdout


def test_short_help_flag():
    """Test the -h flag."""
    result = runner.invoke(app, ["-h"])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert "--help" in result.stdout


def test_show_config_callback():
    """Test the --show-config flag."""
    result = runner.invoke(app, ["--show-config"])
    assert result.exit_code == 0
    assert "Hash CLI Configuration" in result.stdout


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a", LLMProvider.ANTHROPIC),
        ("anthropic", LLMProvider.ANTHROPIC),
        ("g", LLMProvider.GOOGLE),
        ("google", LLMProvider.GOOGLE),
        ("o", LLMProvider.OPENAI),
        ("openai", LLMProvider.OPENAI),
    ],
)
def test_resolve_provider_option_accepts_single_letter_aliases(raw, expected):
    """Provider CLI option should accept both full names and one-letter aliases."""
    assert _resolve_provider_option(raw) == expected


def test_main_provider_short_alias_overrides_config(mocker):
    """Short provider aliases should route through the same CLI override path."""
    config = HashConfig(llm_provider=LLMProvider.OPENAI)
    mocker.patch("hashcli.main.load_configuration", return_value=config)
    mocker.patch("hashcli.main.validate_api_setup")
    llm_mode = mocker.Mock(return_value=None)
    mocker.patch("hashcli.main.execute_llm_mode", llm_mode)
    mocker.patch("hashcli.main.asyncio.run")

    result = runner.invoke(app, ["-pa", "hello"])

    assert result.exit_code == 0
    assert config.llm_provider == LLMProvider.ANTHROPIC
    llm_mode.assert_called_once()


def test_config_wizard_preserves_existing_comments_and_unrelated_settings(mocker, tmp_path):
    """Interactive config should update only chosen keys and preserve comments/unrelated settings."""
    mock_home = tmp_path / "home"
    config_dir = mock_home / ".hashcli"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                "# keep this comment",
                "streaming = true",
                'openai_model = "old-model"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    mocker.patch("hashcli.config.Path.home", return_value=mock_home)
    mocker.patch("hashcli.main.ensure_shell_integration", return_value="skipped")
    mocker.patch("hashcli.main._validate_setup_provider", mocker.AsyncMock(return_value=(True, "ok")))

    result = runner.invoke(app, ["--config"], input="1\nsecret-key\ny\n")

    assert result.exit_code == 0
    content = config_path.read_text(encoding="utf-8")
    assert "# keep this comment" in content
    assert "streaming = true" in content
    assert 'llm_provider = "openai"' in content
    assert 'openai_model = "gpt-5-nano"' in content
    assert 'openai_api_key = "secret-key"' in content


def test_config_runs_shell_setup_when_not_installed(mocker, tmp_path):
    """The config wizard should install shell integration when needed."""
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
    mocker.patch("hashcli.config.Path.home", return_value=tmp_path)
    mocker.patch("hashcli.main._validate_setup_provider", mocker.AsyncMock(return_value=(True, "ok")))

    result = runner.invoke(app, ["--config"], input="1\n1\nsecret-key\nn\n", env={"SHELL": "/bin/zsh"})
    assert result.exit_code == 0
    assert "Installing zsh shell integration" in result.stdout
    assert mock_run.called
    args, _kwargs = mock_run.call_args
    assert args[0][0] == "/bin/bash"
    assert "install.sh" in str(args[0][1])
    assert args[0][2] == "install"


def test_config_skips_shell_setup_when_already_installed(mocker, tmp_path):
    """The config wizard should skip shell integration when already configured."""
    mock_home = tmp_path / "home"
    (mock_home / ".config" / "zsh" / "hash" / "completions").mkdir(parents=True)
    (mock_home / ".config" / "zsh" / "hash" / "hash.zsh").write_text("", encoding="utf-8")
    (mock_home / ".config" / "zsh" / "hash" / "completions" / "_hash").write_text("", encoding="utf-8")
    (mock_home / ".zshrc").write_text("source ~/.config/zsh/hash/hash.zsh\n", encoding="utf-8")
    (mock_home / ".hashcli").mkdir(parents=True)

    mocker.patch("hashcli.main.Path.home", return_value=mock_home)
    mocker.patch("hashcli.config.Path.home", return_value=mock_home)
    mock_run = mocker.patch("hashcli.main.subprocess.run")
    mocker.patch("hashcli.main._validate_setup_provider", mocker.AsyncMock(return_value=(True, "ok")))

    result = runner.invoke(app, ["--config"], input="1\n1\nsecret-key\nn\n", env={"SHELL": "/bin/zsh"})

    assert result.exit_code == 0
    assert "already configured, skipping" in result.stdout
    mock_run.assert_not_called()


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

    result = runner.invoke(app, ["--add-cmd", str(plugin_file), "--yes"])

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

    result = runner.invoke(app, ["--add-cmd", str(plugin_dir), "--yes"])

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

    result = runner.invoke(app, ["--add-cmd", "plugins/hello.py", "--yes"])

    installed_file = mock_home / ".hashcli" / "plugins" / "hello.py"
    assert result.exit_code == 0
    assert installed_file.exists()
    assert "/hello" in result.stdout

    from hashcli.command_proxy import CommandProxy

    proxy = CommandProxy(HashConfig())
    assert "hello" in proxy.get_available_commands()
    assert "Hello from" in proxy.execute("/hello")


def test_add_cmd_requires_yes_for_noninteractive_install(mocker, tmp_path):
    """Non-interactive plugin installs should require --yes."""
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

    assert result.exit_code == 1
    assert "without --yes" in result.stdout
    assert not (mock_home / ".hashcli" / "plugins" / "hello.py").exists()


def test_add_cmd_subprocess_validation_failure_does_not_install(mocker, tmp_path):
    """Plugins that fail import validation should not be copied into the plugin dir."""
    plugin_file = tmp_path / "bad_import.py"
    plugin_file.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    mock_home = tmp_path / "home"
    mock_home.mkdir()
    mocker.patch("hashcli.command_proxy.Path.home", return_value=mock_home)

    result = runner.invoke(app, ["--add-cmd", str(plugin_file), "--yes"])

    assert result.exit_code == 1
    assert "Plugin validation failed before install" in result.stdout
    assert not (mock_home / ".hashcli" / "plugins" / "bad_import.py").exists()


def test_list_plugins_and_remove_cmd(mocker, tmp_path):
    """Installed plugins should be discoverable and removable."""
    mock_home = tmp_path / "home"
    plugin_dir = mock_home / ".hashcli" / "plugins"
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
                "        return 'hello world'",
                "",
                "    def get_help(self) -> str:",
                "        return 'say hello'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    mocker.patch("hashcli.command_proxy.Path.home", return_value=mock_home)

    listed = runner.invoke(app, ["--list-plugins"])
    removed = runner.invoke(app, ["--remove-cmd", "hello", "--yes"])

    assert listed.exit_code == 0
    assert "/hello" in listed.stdout
    assert "HelloCommand" in listed.stdout
    assert removed.exit_code == 0
    assert not plugin_file.exists()


def test_completion_commands_include_installed_plugins(mocker, tmp_path):
    """Completion command output should include dynamically installed plugin commands."""
    mock_home = tmp_path / "home"
    plugin_dir = mock_home / ".hashcli" / "plugins"
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
                "        return 'hello world'",
                "",
                "    def get_help(self) -> str:",
                "        return 'say hello'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    mocker.patch("hashcli.command_proxy.Path.home", return_value=mock_home)

    result = runner.invoke(app, ["--completion-commands"])

    assert result.exit_code == 0
    assert "hello\tsay hello" in result.stdout


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
        command_confirmation=False,
    )
    assert policy.force_tool_confirmation is True
    assert policy.command_confirmation is True
    assert policy.force_command_oriented is True


def test_build_query_execution_policy_action_query_respects_config():
    """Action query without how-to should follow config confirmation."""
    normalized = _normalize_shell_input("# find all __pycache__ in current dir")
    policy_false = _build_query_execution_policy(
        "# find all __pycache__ in current dir",
        normalized,
        command_confirmation=False,
    )
    policy_true = _build_query_execution_policy(
        "# find all __pycache__ in current dir",
        normalized,
        command_confirmation=True,
    )
    assert policy_false.force_tool_confirmation is None
    assert policy_false.command_confirmation is False
    assert policy_true.force_tool_confirmation is None
    assert policy_true.command_confirmation is True


def test_build_query_execution_policy_command_hint_disables_confirmation():
    """Embedded command-hint mode should execute without confirmation prompts."""
    normalized = _normalize_shell_input("find # all __pycache__ folders")
    policy = _build_query_execution_policy(
        "find # all __pycache__ folders",
        normalized,
        command_confirmation=True,
    )
    assert policy.force_tool_confirmation is False
    assert policy.command_confirmation is False
    assert policy.force_command_oriented is True


def test_build_shell_scope_fingerprint_contains_parent_and_tty(mocker):
    """Shell fingerprint should include parent PID and tty identity."""
    mocker.patch("hashcli.main.os.getppid", return_value=12345)
    mocker.patch("hashcli.main._get_active_tty", return_value="/dev/pts/4")
    mocker.patch.dict(
        "hashcli.main.os.environ",
        {
            "TMUX": "tmux-1",
            "STY": "",
            "TERM_SESSION_ID": "term-session",
            "WT_SESSION": "",
            "TERM_PROGRAM": "iTerm.app",
        },
        clear=True,
    )

    fingerprint = _build_shell_scope_fingerprint()

    assert fingerprint.startswith("12345|/dev/pts/4|")
    assert "term-session" in fingerprint
    assert "iTerm.app" in fingerprint


def test_resolve_conversation_session_id_prefers_env_var(mocker):
    """Explicit HASHCLI_SESSION_ID should remain the highest-priority source."""
    mocker.patch.dict("hashcli.main.os.environ", {"HASHCLI_SESSION_ID": "env-session"}, clear=True)
    assert _resolve_conversation_session_id() == "env-session"


def test_resolve_conversation_session_id_noninteractive_returns_none(mocker):
    """Non-interactive invocation should keep one-shot behavior by default."""
    mocker.patch.dict("hashcli.main.os.environ", {}, clear=True)
    mocker.patch("hashcli.main._is_interactive_session", return_value=False)
    assert _resolve_conversation_session_id() is None


def test_resolve_conversation_session_id_interactive_is_stable(mocker):
    """Interactive shell invocations without env should map to a stable shell session id."""
    mocker.patch.dict("hashcli.main.os.environ", {}, clear=True)
    mocker.patch("hashcli.main._is_interactive_session", return_value=True)
    mocker.patch("hashcli.main._build_shell_scope_fingerprint", return_value="ppid|tty|session")

    first = _resolve_conversation_session_id()
    second = _resolve_conversation_session_id()

    expected = "shell-" + hashlib.sha256("ppid|tty|session".encode("utf-8")).hexdigest()[:24]
    assert first == expected
    assert second == expected


def test_resolve_conversation_session_id_new_session_overrides_existing(mocker):
    """--new-session should force a fresh session id even when env id exists."""
    forced_uuid = uuid.UUID("11111111-2222-3333-4444-555555555555")
    mocker.patch.dict("hashcli.main.os.environ", {"HASHCLI_SESSION_ID": "env-session"}, clear=True)
    mocker.patch("hashcli.main.uuid.uuid4", return_value=forced_uuid)

    resolved = _resolve_conversation_session_id(new_session=True)

    assert resolved == str(forced_uuid)
    assert resolved != "env-session"


def test_resolve_conversation_session_id_requested_prefix(sample_config, temp_dir):
    """--session should resolve a unique historical session prefix."""
    from hashcli.history import ConversationHistory

    sample_config.history_dir = temp_dir / "history"
    history = ConversationHistory(sample_config.history_dir)
    session_id = "abcdef12-1234-1234-1234-123456789abc"
    history.start_session(session_id=session_id)

    assert _resolve_conversation_session_id(requested_session_id="abcdef12", config=sample_config) == session_id


def test_resolve_conversation_session_id_rejects_new_session_conflict(sample_config):
    """--session and --new-session are mutually exclusive."""
    with pytest.raises(Exception, match="cannot be combined"):
        _resolve_conversation_session_id(new_session=True, requested_session_id="abc", config=sample_config)


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
    response = "do you want execute `echo {5..20}`?\nWould you like me to execute `seq 5 20` to show you the output?"
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
    assert call_kwargs["command_confirmation"] is False
    assert call_kwargs["force_command_oriented"] is True


def test_main_new_session_routes_with_new_session_id(mocker):
    """--new-session should resolve and forward a fresh session id."""
    config = HashConfig()
    mocker.patch("hashcli.main.load_configuration", return_value=config)
    mocker.patch("hashcli.main.validate_api_setup")
    mock_resolve = mocker.patch("hashcli.main._resolve_conversation_session_id", return_value="new-session-id")
    llm_mode = mocker.Mock(return_value=None)
    mocker.patch("hashcli.main.execute_llm_mode", llm_mode)
    mocker.patch("hashcli.main.asyncio.run")

    result = runner.invoke(app, ["--new-session", "hello"])

    assert result.exit_code == 0
    mock_resolve.assert_called_once_with(new_session=True, requested_session_id=None, config=config)
    assert llm_mode.call_args.kwargs["session_id"] == "new-session-id"


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
async def test_llm_mode_passes_session_id_to_handler(mocker):
    """Resolved session id should be passed into handler construction."""
    mock_chat = mocker.AsyncMock(return_value="llm output")
    mock_handler = mocker.patch("hashcli.main.LLMHandler")
    mock_instance = mock_handler.return_value
    mock_instance.chat = mock_chat

    config = HashConfig()
    await execute_llm_mode("hello", config, quiet=True, session_id="shell-session-id")

    mock_handler.assert_called_once_with(config, session_id="shell-session-id")
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
    assert mock_maybe_execute.call_args.kwargs["command_confirmation"] is True


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
        command_confirmation=False,
        force_command_oriented=True,
    )

    mock_maybe_execute.assert_awaited_once()
    assert mock_maybe_execute.call_args.kwargs["command_confirmation"] is False


@pytest.mark.asyncio
async def test_maybe_execute_suggested_command_uses_passthrough_output(mocker):
    """Suggested command execution should stream child prompts directly to the terminal."""
    mock_execute = mocker.AsyncMock(return_value="")
    mocker.patch("hashcli.tools.shell.ShellTool.execute", mock_execute)

    config = HashConfig()
    await _maybe_execute_suggested_command(
        "To clean up Docker images, run:\n```bash\ndocker image prune -a\n```",
        config,
        quiet=True,
        user_query="clean up docker images Im not using",
        command_confirmation=False,
    )

    mock_execute.assert_awaited_once()
    arguments = mock_execute.await_args.args[0]
    assert arguments["command"] == "docker image prune -a"
    assert arguments["passthrough_output"] is True


@pytest.mark.asyncio
async def test_maybe_execute_suggested_command_prompts_for_destructive_command_even_when_disabled(mocker):
    """Destructive suggestions must still ask before executing."""
    mocker.patch("hashcli.main._is_interactive_session", return_value=False)
    mock_execute = mocker.AsyncMock(return_value="")
    mocker.patch("hashcli.tools.shell.ShellTool.execute", mock_execute)

    config = HashConfig()
    await _maybe_execute_suggested_command(
        "SUGGESTED_COMMAND: kill -9 1234",
        config,
        quiet=True,
        user_query="kill whatever is running on port 8080",
        command_confirmation=False,
    )

    mock_execute.assert_not_called()


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
