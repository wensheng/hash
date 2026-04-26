"""Unit tests for configuration management."""

import pytest
import os
import tempfile
from pathlib import Path

from hashcli.config import (
    ConfigurationError,
    HashConfig,
    LLMProvider,
    load_configuration,
    parse_config_value,
    remove_config_keys,
    save_config,
    update_config_values,
    get_config_paths,
    load_environment_variables,
)


class TestHashConfig:
    """Test the HashConfig class."""

    def test_default_configuration(self):
        """Test default configuration values."""
        config = HashConfig()

        assert config.llm_provider == LLMProvider.GOOGLE
        assert config.openai_model == "gpt-5-nano"
        assert config.allow_command_execution is True
        assert config.command_confirmation is False
        assert config.tool_confirmation is False
        assert config.allow_shell_operators is True
        assert config.history_enabled is True
        assert config.rich_output is True
        assert config.streaming is False

    def test_get_current_model(self):
        """Test getting current model for different providers."""
        # OpenAI
        config = HashConfig(llm_provider=LLMProvider.OPENAI, openai_model="gpt-3.5-turbo")
        assert config.get_current_model() == "gpt-3.5-turbo"

        # Anthropic
        config = HashConfig(llm_provider=LLMProvider.ANTHROPIC, anthropic_model="claude-3-sonnet")
        assert config.get_current_model() == "claude-3-sonnet"

        # Google
        config = HashConfig(llm_provider=LLMProvider.GOOGLE, google_model="gemini-pro")
        assert config.get_current_model() == "gemini-pro"

    def test_get_current_api_key(self):
        """Test getting current API key for different providers."""
        # OpenAI
        config = HashConfig(llm_provider=LLMProvider.OPENAI, openai_api_key="openai-key")
        assert config.get_current_api_key() == "openai-key"

        # Anthropic
        config = HashConfig(llm_provider=LLMProvider.ANTHROPIC, anthropic_api_key="anthropic-key")
        assert config.get_current_api_key() == "anthropic-key"

    def test_validate_current_setup(self):
        """Test configuration validation."""
        # Valid setup
        config = HashConfig(llm_provider=LLMProvider.OPENAI, openai_api_key="valid-key")
        assert config.validate_current_setup() is True

        # Invalid setup (no API key)
        config = HashConfig(llm_provider=LLMProvider.OPENAI, openai_api_key=None)
        assert config.validate_current_setup() is False

        # Invalid setup (empty API key)
        config = HashConfig(llm_provider=LLMProvider.OPENAI, openai_api_key="")
        assert config.validate_current_setup() is False


class TestConfigurationLoading:
    """Test configuration loading from various sources."""

    def test_load_environment_variables(self):
        """Test loading configuration from environment variables."""
        # Set test environment variables
        os.environ["HASHCLI_LLM_PROVIDER"] = "anthropic"
        os.environ["HASHCLI_ANTHROPIC_MODEL"] = "claude-3-opus"
        os.environ["HASHCLI_ALLOW_COMMAND_EXECUTION"] = "false"
        os.environ["HASHCLI_COMMAND_CONFIRMATION"] = "true"
        os.environ["HASHCLI_TOOL_CONFIRMATION"] = "false"
        os.environ["HASHCLI_ALLOW_SHELL_OPERATORS"] = "true"

        try:
            env_config = load_environment_variables()

            assert env_config["llm_provider"] == "anthropic"
            assert env_config["anthropic_model"] == "claude-3-opus"
            assert env_config["allow_command_execution"] is False
            assert env_config["command_confirmation"] is True
            assert env_config["tool_confirmation"] is False
            assert env_config["allow_shell_operators"] is True

        finally:
            # Clean up
            for key in [
                "HASHCLI_LLM_PROVIDER",
                "HASHCLI_ANTHROPIC_MODEL",
                "HASHCLI_ALLOW_COMMAND_EXECUTION",
                "HASHCLI_COMMAND_CONFIRMATION",
                "HASHCLI_TOOL_CONFIRMATION",
                "HASHCLI_ALLOW_SHELL_OPERATORS",
            ]:
                if key in os.environ:
                    del os.environ[key]

    def test_load_environment_variables_maps_legacy_require_confirmation(self):
        """Legacy confirmation env var should map to both split settings."""
        os.environ["HASHCLI_REQUIRE_CONFIRMATION"] = "true"

        try:
            env_config = load_environment_variables()

            assert "require_confirmation" not in env_config
            assert env_config["command_confirmation"] is True
            assert env_config["tool_confirmation"] is True
        finally:
            del os.environ["HASHCLI_REQUIRE_CONFIRMATION"]

    def test_load_configuration_with_overrides(self):
        """Test configuration loading with parameter overrides."""
        config = load_configuration(debug=True, model_override="gpt-3.5-turbo")

        assert config.show_debug is True
        assert config.openai_model == "gpt-3.5-turbo"

    def test_load_configuration_maps_legacy_require_confirmation(self, temp_dir):
        """Legacy require_confirmation should map to both split confirmation settings."""
        config_path = temp_dir / "config.toml"
        config_path.write_text("require_confirmation = true\n", encoding="utf-8")

        config = load_configuration(config_file=str(config_path))

        assert config.command_confirmation is True
        assert config.tool_confirmation is True

    def test_save_and_load_config(self, temp_dir):
        """Test saving and loading configuration files."""
        config = HashConfig(
            llm_provider=LLMProvider.ANTHROPIC,
            anthropic_model="claude-3-sonnet",
            allow_command_execution=False,
            history_enabled=True,
        )

        config_path = temp_dir / "test_config.toml"

        # Save configuration
        success = save_config(config, config_path)
        assert success is True
        assert config_path.exists()

        # Verify file contents
        with open(config_path, "r") as f:
            content = f.read()
            assert 'llm_provider = "anthropic"' in content
            assert 'anthropic_model = "claude-3-sonnet"' in content
            assert "allow_command_execution = false" in content

    def test_get_config_paths(self):
        """Test getting configuration file paths."""
        paths = get_config_paths()

        assert len(paths) >= 1
        assert all(isinstance(p, Path) for p in paths)
        assert any(p.name == "config.toml" for p in paths)

    def test_load_standard_api_keys_from_env(self, monkeypatch):
        """Test that standard API keys are loaded from the environment."""
        # Unset HASHCLI_ prefixed keys to ensure we are testing the fallback
        monkeypatch.delenv("HASHCLI_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("HASHCLI_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HASHCLI_GOOGLE_API_KEY", raising=False)

        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
        monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
        monkeypatch.setenv("GEMINI_API_KEY", "test-google-key")

        config = load_configuration()

        assert config.openai_api_key == "test-openai-key"
        assert config.anthropic_api_key == "test-anthropic-key"
        assert config.google_api_key == "test-google-key"

    def test_standard_api_keys_override_config_file(self, monkeypatch, temp_dir):
        """Test that standard API keys override config file values."""
        monkeypatch.delenv("HASHCLI_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("HASHCLI_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HASHCLI_GOOGLE_API_KEY", raising=False)

        monkeypatch.setenv("OPENAI_API_KEY", "env-openai-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-anthropic-key")
        monkeypatch.setenv("GOOGLE_API_KEY", "env-google-key")
        monkeypatch.setenv("GEMINI_API_KEY", "env-google-key")

        config_path = temp_dir / "config.toml"
        config_path.write_text(
            "\n".join(
                [
                    'openai_api_key = "file-openai-key"',
                    'anthropic_api_key = "file-anthropic-key"',
                    'google_api_key = "file-google-key"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        config = load_configuration(config_file=str(config_path))

        assert config.openai_api_key == "env-openai-key"
        assert config.anthropic_api_key == "env-anthropic-key"
        assert config.google_api_key == "env-google-key"

    def test_update_config_values_preserves_comments_and_unrelated_settings(self, temp_dir):
        """Targeted config updates should not rewrite unrelated settings or strip comments."""
        config_path = temp_dir / "config.toml"
        config_path.write_text(
            "\n".join(
                [
                    "# user note",
                    "streaming = true",
                    'openai_model = "old-model"',
                    'openai_api_key = "old-key"',
                    "# another note",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        success = update_config_values(
            {
                "llm_provider": "openai",
                "openai_model": "gpt-5-mini",
                "openai_api_key": "new-key",
            },
            config_path=config_path,
        )

        assert success is True
        content = config_path.read_text(encoding="utf-8")
        assert "# user note" in content
        assert "# another note" in content
        assert "streaming = true" in content
        assert 'openai_model = "gpt-5-mini"' in content
        assert 'openai_api_key = "new-key"' in content
        assert 'llm_provider = "openai"' in content

    def test_remove_config_keys_preserves_comments(self, temp_dir):
        """Unset helper should remove assignments without stripping comments."""
        config_path = temp_dir / "config.toml"
        config_path.write_text(
            '# note\nstreaming = true\nopenai_model = "old"\n# keep\n',
            encoding="utf-8",
        )

        assert remove_config_keys(["streaming"], config_path=config_path) is True

        content = config_path.read_text(encoding="utf-8")
        assert "# note" in content
        assert "# keep" in content
        assert "streaming =" not in content
        assert 'openai_model = "old"' in content

    def test_parse_config_value_by_field_type(self):
        """CLI config values should parse according to target field type."""
        assert parse_config_value("streaming", "true") is True
        assert parse_config_value("max_response_tokens", "2048") == 2048
        assert parse_config_value("blocked_commands", '["rm -rf", "sudo"]') == ["rm -rf", "sudo"]
        assert parse_config_value("openai_model", "gpt-custom") == "gpt-custom"
        assert parse_config_value("llm_provider", "anthropic") == "anthropic"

        with pytest.raises(ConfigurationError):
            parse_config_value("streaming", "sometimes")


class TestProviderEnum:
    """Test the LLMProvider enum."""

    def test_provider_values(self):
        """Test provider enum values."""
        assert LLMProvider.OPENAI.value == "openai"
        assert LLMProvider.ANTHROPIC.value == "anthropic"
        assert LLMProvider.GOOGLE.value == "google"

    def test_provider_creation_from_string(self):
        """Test creating provider from string value."""
        assert LLMProvider("openai") == LLMProvider.OPENAI
        assert LLMProvider("anthropic") == LLMProvider.ANTHROPIC
        assert LLMProvider("google") == LLMProvider.GOOGLE

        with pytest.raises(ValueError):
            LLMProvider("invalid_provider")
