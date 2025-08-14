"""Unit tests for configuration management."""

import pytest
import os
import tempfile
from pathlib import Path

from hashcli.config import (
    HashConfig, LLMProvider, load_configuration, 
    save_config, get_config_paths, load_environment_variables
)


class TestHashConfig:
    """Test the HashConfig class."""
    
    def test_default_configuration(self):
        """Test default configuration values."""
        config = HashConfig()
        
        assert config.llm_provider == LLMProvider.OPENAI
        assert config.openai_model == "gpt-5-nano"
        assert config.allow_command_execution is True
        assert config.require_confirmation is True
        assert config.history_enabled is True
        assert config.rich_output is True
    
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
        config = HashConfig(
            llm_provider=LLMProvider.OPENAI,
            openai_api_key="openai-key"
        )
        assert config.get_current_api_key() == "openai-key"
        
        # Anthropic
        config = HashConfig(
            llm_provider=LLMProvider.ANTHROPIC,
            anthropic_api_key="anthropic-key"
        )
        assert config.get_current_api_key() == "anthropic-key"
    
    def test_validate_current_setup(self):
        """Test configuration validation."""
        # Valid setup
        config = HashConfig(
            llm_provider=LLMProvider.OPENAI,
            openai_api_key="valid-key"
        )
        assert config.validate_current_setup() is True
        
        # Invalid setup (no API key)
        config = HashConfig(
            llm_provider=LLMProvider.OPENAI,
            openai_api_key=None
        )
        assert config.validate_current_setup() is False
        
        # Invalid setup (empty API key)
        config = HashConfig(
            llm_provider=LLMProvider.OPENAI,
            openai_api_key=""
        )
        assert config.validate_current_setup() is False


class TestConfigurationLoading:
    """Test configuration loading from various sources."""
    
    def test_load_environment_variables(self):
        """Test loading configuration from environment variables."""
        # Set test environment variables
        os.environ['HASHCLI_LLM_PROVIDER'] = 'anthropic'
        os.environ['HASHCLI_ANTHROPIC_MODEL'] = 'claude-3-opus'
        os.environ['HASHCLI_ALLOW_COMMAND_EXECUTION'] = 'false'
        os.environ['HASHCLI_REQUIRE_CONFIRMATION'] = 'true'
        
        try:
            env_config = load_environment_variables()
            
            assert env_config['llm_provider'] == 'anthropic'
            assert env_config['anthropic_model'] == 'claude-3-opus'
            assert env_config['allow_command_execution'] is False
            assert env_config['require_confirmation'] is True
            
        finally:
            # Clean up
            for key in ['HASHCLI_LLM_PROVIDER', 'HASHCLI_ANTHROPIC_MODEL', 
                       'HASHCLI_ALLOW_COMMAND_EXECUTION', 'HASHCLI_REQUIRE_CONFIRMATION']:
                if key in os.environ:
                    del os.environ[key]
    
    def test_load_configuration_with_overrides(self):
        """Test configuration loading with parameter overrides."""
        config = load_configuration(debug=True, model_override="gpt-3.5-turbo")
        
        assert config.show_debug is True
        assert config.openai_model == "gpt-3.5-turbo"
    
    def test_save_and_load_config(self, temp_dir):
        """Test saving and loading configuration files."""
        config = HashConfig(
            llm_provider=LLMProvider.ANTHROPIC,
            anthropic_model="claude-3-sonnet",
            allow_command_execution=False,
            history_enabled=True
        )
        
        config_path = temp_dir / "test_config.toml"
        
        # Save configuration
        success = save_config(config, config_path)
        assert success is True
        assert config_path.exists()
        
        # Verify file contents
        with open(config_path, 'r') as f:
            content = f.read()
            assert 'llm_provider = "anthropic"' in content
            assert 'anthropic_model = "claude-3-sonnet"' in content
            assert 'allow_command_execution = false' in content
    
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

        config = load_configuration()

        assert config.openai_api_key == "test-openai-key"
        assert config.anthropic_api_key == "test-anthropic-key"
        assert config.google_api_key == "test-google-key"


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