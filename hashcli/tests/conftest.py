"""Test configuration for pytest."""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock

from hashcli.config import HashConfig, LLMProvider


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def sample_config():
    """Create a sample configuration for testing."""
    config = HashConfig(
        llm_provider=LLMProvider.OPENAI,
        openai_api_key="test-key-123",
        openai_model="gpt-4",
        allow_command_execution=True,
        require_confirmation=False,  # Disable for testing
        history_enabled=True,
        show_debug=False,
    )
    return config


@pytest.fixture
def mock_llm_provider():
    """Create a mock LLM provider for testing."""
    provider = MagicMock()
    provider.generate_response.return_value = MagicMock(
        content="Test response", tool_calls=[], model="test-model"
    )
    return provider


@pytest.fixture(autouse=True)
def setup_test_environment(temp_dir):
    """Set up test environment variables."""
    import os

    # Set test API keys
    os.environ["HASHCLI_OPENAI_API_KEY"] = "test-key-openai"
    os.environ["HASHCLI_ANTHROPIC_API_KEY"] = "test-key-anthropic"
    os.environ["HASHCLI_GOOGLE_API_KEY"] = "test-key-google"

    # Set test history directory
    os.environ["HASHCLI_HISTORY_DIR"] = str(temp_dir / "history")

    yield

    # Clean up environment
    test_keys = [
        "HASHCLI_OPENAI_API_KEY",
        "HASHCLI_ANTHROPIC_API_KEY",
        "HASHCLI_GOOGLE_API_KEY",
        "HASHCLI_HISTORY_DIR",
    ]
    for key in test_keys:
        if key in os.environ:
            del os.environ[key]
