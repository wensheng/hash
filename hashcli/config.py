"""Configuration management for Hash CLI with multi-source loading."""

import os
import platform
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

if platform.python_version_tuple() < ("3", "11"):
    import tomli
else:
    import tomllib

import tomli_w
from pydantic import BaseModel, Field, validator


class LogLevel(str, Enum):
    """Available logging levels."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LLMProvider(str, Enum):
    """Available LLM providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"


class HashConfig(BaseModel):
    """Main configuration class with validation and multi-source loading."""

    # LLM Configuration
    llm_provider: LLMProvider = Field(default=LLMProvider.GOOGLE, description="Default LLM provider")
    openai_api_key: Optional[str] = Field(default="null", description="OpenAI API key")
    openai_base_url: Optional[str] = Field(default=None, description="OpenAI base URL")
    openai_model: str = Field(default="gpt-5-nano", description="Default OpenAI model")
    openai_reasoning_effort: Optional[str] = Field(
        default="low",
        description="Reasoning effort for GPT-5 models (none/minimal/low/medium/high/xhigh)",
    )
    openai_text_verbosity: Optional[str] = Field(
        default="low", description="Text verbosity for GPT-5 models (low/medium/high)"
    )
    anthropic_api_key: Optional[str] = Field(default=None, description="Anthropic API key")
    anthropic_model: str = Field(default="claude-haiku-4-5-20251001", description="Default Anthropic model")
    google_api_key: Optional[str] = Field(default=None, description="Google AI API key")
    google_model: str = Field(default="gemini-2.5-flash-lite", description="Default Google model")
    max_response_tokens: int = Field(default=4096, description="Maximum tokens in LLM responses")

    # Tool Configuration
    allow_command_execution: bool = Field(default=True, description="Allow LLM to execute shell commands")
    require_confirmation: bool = Field(default=False, description="Require user confirmation for tool calls")
    command_timeout: int = Field(default=30, description="Command execution timeout in seconds")
    allow_shell_operators: bool = Field(
        default=True,
        description="Allow shell operators like | and ; in commands (requires shell execution)",
    )

    # History Configuration
    history_enabled: bool = Field(default=True, description="Enable conversation history")
    history_dir: Optional[Path] = Field(default=None, description="History storage directory")
    max_history_size: int = Field(default=1000, description="Maximum history entries")
    history_retention_days: int = Field(default=30, description="History retention period")

    # Output Configuration
    rich_output: bool = Field(default=True, description="Enable rich text formatting")
    streaming: bool = Field(default=False, description="Enable streaming responses from providers")
    show_debug: bool = Field(default=False, description="Show debug information")
    log_level: LogLevel = Field(default=LogLevel.INFO, description="Logging level")

    # Security Configuration
    sandbox_commands: bool = Field(default=False, description="Run commands in sandbox")
    allowed_commands: Optional[List[str]] = Field(default=None, description="Whitelist of allowed commands")
    blocked_commands: List[str] = Field(
        default_factory=lambda: ["rm -rf", "sudo", "su"],
        description="Blacklist of blocked commands",
    )

    class Config:
        env_prefix = "HASHCMD_"
        case_sensitive = False

    @validator("history_dir", pre=True, always=True)
    def set_default_history_dir(cls, v):
        """Set default history directory if not provided."""
        if v is None:
            return Path.home() / ".hashcli" / "history"
        return Path(v) if isinstance(v, str) else v

    @validator("openai_api_key", "anthropic_api_key", "google_api_key", pre=True)
    def validate_api_keys(cls, v):
        """Validate and sanitize API keys."""
        if v and isinstance(v, str):
            return v.strip()
        return v

    @validator("max_response_tokens", pre=True)
    def validate_max_response_tokens(cls, v):
        """Ensure response token limit is a positive integer."""
        if v is None:
            return v
        try:
            value = int(v)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_response_tokens must be an integer") from exc
        if value <= 0:
            raise ValueError("max_response_tokens must be positive")
        return value

    def get_current_model(self) -> str:
        """Get the current model for the selected provider."""
        if self.llm_provider == LLMProvider.OPENAI:
            return self.openai_model
        elif self.llm_provider == LLMProvider.ANTHROPIC:
            return self.anthropic_model
        elif self.llm_provider == LLMProvider.GOOGLE:
            return self.google_model
        else:
            raise ValueError(f"Unknown provider: {self.llm_provider}")

    def get_current_api_key(self) -> Optional[str]:
        """Get the API key for the current provider."""
        if self.llm_provider == LLMProvider.OPENAI:
            return self.openai_api_key
        elif self.llm_provider == LLMProvider.ANTHROPIC:
            return self.anthropic_api_key
        elif self.llm_provider == LLMProvider.GOOGLE:
            return self.google_api_key
        else:
            raise ValueError(f"Unknown provider: {self.llm_provider}")

    def validate_current_setup(self) -> bool:
        """Validate that current provider has necessary configuration."""
        api_key = self.get_current_api_key()
        return api_key is not None and len(api_key.strip()) > 0


def get_config_paths() -> List[Path]:
    """Get configuration file paths in priority order."""
    paths = []

    # User config directory
    user_config_dir = Path.home() / ".hashcli"
    paths.append(user_config_dir / "config.toml")

    # System config directory
    if os.name == "posix":  # Unix/Linux/macOS
        paths.append(Path("/etc/hashcli/config.toml"))
    elif os.name == "nt":  # Windows
        paths.append(Path(os.environ.get("ProgramData", "C:/ProgramData")) / "hashcli" / "config.toml")

    return paths


def load_config_file(config_path: Path) -> Dict[str, Any]:
    """Load configuration from a TOML file."""
    try:
        if config_path.exists():
            with open(config_path, "rb") as f:
                return tomllib.load(f)
    except Exception:
        # Silent failure for config files - we'll use defaults
        pass
    return {}


def load_environment_variables() -> Dict[str, Any]:
    """Load configuration from environment variables."""
    config = {}
    prefix = "HASHCLI_"

    for key, value in os.environ.items():
        if key.startswith(prefix):
            config_key = key[len(prefix) :].lower()

            # Handle boolean values
            if value.lower() in ("true", "1", "yes", "on"):
                config[config_key] = True
            elif value.lower() in ("false", "0", "no", "off"):
                config[config_key] = False
            else:
                # Try to convert to int, fallback to string
                try:
                    config[config_key] = int(value)
                except ValueError:
                    config[config_key] = value

    return config


def load_configuration(
    config_file: Optional[str] = None,
    debug: bool = False,
    model_override: Optional[str] = None,
) -> HashConfig:
    """Load configuration from multiple sources with priority handling.

    Priority order (highest to lowest):
    1. Function parameters (config_file, debug, model_override)
    2. Environment variables (HASHCMD_*)
    3. User config file (~/.hashcli/config.toml)
    4. System config file (/etc/hashcli/config.toml)
    5. Default values
    """
    # Get the primary config path
    primary_config_path = Path.home() / ".hashcli" / "config.toml"

    # Start with empty config
    merged_config = {}
    config_loaded_from_file = False

    # Load from config files (lowest priority)
    config_paths = get_config_paths()
    if config_file:
        # If specific config file provided, use it first
        config_paths.insert(0, Path(config_file))

    for path in reversed(config_paths):  # Reverse to maintain priority
        file_config = load_config_file(path)
        if file_config:
            merged_config.update(file_config)
            config_loaded_from_file = True

    # Load from environment variables (higher priority)
    env_config = load_environment_variables()
    merged_config.update(env_config)

    # Apply function parameters (highest priority)
    if debug:
        merged_config["show_debug"] = True
        merged_config["log_level"] = LogLevel.DEBUG

    if model_override:
        # Determine provider from model name and set appropriately
        model_lower = model_override.lower()
        if "gpt" in model_lower or "openai" in model_lower:
            merged_config["llm_provider"] = LLMProvider.OPENAI
            merged_config["openai_model"] = model_override
        elif "claude" in model_lower or "anthropic" in model_lower:
            merged_config["llm_provider"] = LLMProvider.ANTHROPIC
            merged_config["anthropic_model"] = model_override
        elif "gemini" in model_lower or "google" in model_lower:
            merged_config["llm_provider"] = LLMProvider.GOOGLE
            merged_config["google_model"] = model_override

    # Create and validate configuration
    try:
        config = HashConfig(**merged_config)
    except Exception as e:
        # If configuration is invalid, create default config and show warning
        config = HashConfig()
        if debug:
            print(f"Warning: Invalid configuration, using defaults. Error: {e}")

        # Save the default/fallback configuration
        save_config(config, primary_config_path)

    # After creating config, apply standard API key env overrides.
    def get_nonempty_env_value(env_var: str) -> Optional[str]:
        value = os.environ.get(env_var)
        if value is None:
            return None
        value = value.strip()
        return value or None

    if "HASHCLI_OPENAI_API_KEY" not in os.environ:
        openai_key = get_nonempty_env_value("OPENAI_API_KEY")
        if openai_key:
            config.openai_api_key = openai_key

    if "HASHCLI_ANTHROPIC_API_KEY" not in os.environ:
        anthropic_key = get_nonempty_env_value("ANTHROPIC_API_KEY")
        if anthropic_key:
            config.anthropic_api_key = anthropic_key

    if "HASHCLI_GOOGLE_API_KEY" not in os.environ:
        google_key = get_nonempty_env_value("GEMINI_API_KEY") or get_nonempty_env_value("GOOGLE_API_KEY")
        if google_key:
            config.google_api_key = google_key

    # If no config file was loaded, save the default one
    if not config_loaded_from_file:
        save_config(config, primary_config_path)

    return config


def save_config(config: HashConfig, config_path: Optional[Path] = None) -> bool:
    """Save configuration to file."""
    if config_path is None:
        config_path = Path.home() / ".hashcli" / "config.toml"

    try:
        # Ensure directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure history directory exists if enabled
        if config.history_enabled and config.history_dir:
            config.history_dir.mkdir(parents=True, exist_ok=True)

        # Convert config to dict, excluding None values
        config_dict = config.model_dump(
            exclude_none=True,
        )

        # Convert enums to their string values for TOML serialization
        for key, value in config_dict.items():
            if hasattr(value, "value"):  # It's an enum
                config_dict[key] = value.value

        # Convert Path objects to strings for TOML serialization
        for key, value in config_dict.items():
            if isinstance(value, Path):
                config_dict[key] = str(value)

        # Write to file
        with open(config_path, "wb") as f:
            tomli_w.dump(config_dict, f)

        return True

    except Exception:
        return False


def _normalize_toml_value(value: Any) -> Any:
    """Normalize values for TOML serialization."""
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_normalize_toml_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_toml_value(item) for key, item in value.items()}
    return value


def update_config_values(updates: Dict[str, Any], config_path: Optional[Path] = None) -> bool:
    """Update only specified top-level keys in a TOML config file, preserving comments and other settings."""
    if config_path is None:
        config_path = Path.home() / ".hashcli" / "config.toml"

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)

        normalized_updates = {
            key: _normalize_toml_value(value)
            for key, value in updates.items()
            if value is not None
        }

        serialized_assignments = {
            key: tomli_w.dumps({key: value}).strip()
            for key, value in normalized_updates.items()
        }

        if not config_path.exists():
            content = "\n".join(serialized_assignments[key] for key in normalized_updates)
            config_path.write_text(f"{content}\n" if content else "", encoding="utf-8")
            return True

        lines = config_path.read_text(encoding="utf-8").splitlines()
        updated_keys = set()

        for index, line in enumerate(lines):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue

            for key, assignment in serialized_assignments.items():
                pattern = re.compile(rf"^(\s*){re.escape(key)}(\s*=\s*)(.*?)(\s*(#.*))?$")
                match = pattern.match(line)
                if match is None:
                    continue

                leading_ws = match.group(1)
                inline_comment = match.group(4) or ""
                lines[index] = f"{leading_ws}{assignment}{inline_comment}"
                updated_keys.add(key)
                break

        missing_keys = [key for key in normalized_updates if key not in updated_keys]
        if missing_keys:
            if lines and lines[-1] != "":
                lines.append("")
            for key in missing_keys:
                lines.append(serialized_assignments[key])

        final_text = "\n".join(lines)
        if lines:
            final_text += "\n"
        config_path.write_text(final_text, encoding="utf-8")
        return True

    except Exception:
        return False


# Configuration validation and helper functions
class ConfigurationError(Exception):
    """Configuration-related errors."""

    pass


def validate_api_setup(config: HashConfig) -> None:
    """Validate that API setup is correct for current provider."""
    if not config.validate_current_setup():
        provider = config.llm_provider.value
        env_var = f"{provider.upper()}_API_KEY"
        raise ConfigurationError(
            f"No API key configured for {provider}. Set {env_var} environment variable or add to config file."
        )


def get_model_options(provider: LLMProvider) -> List[str]:
    """Get available model options for a provider."""
    if provider == LLMProvider.OPENAI:
        return [
            "gpt-5.2",
            "gpt-5.1-codex-mini",
            "gpt-5-mini",
            "gpt-5-nano",
        ]
    elif provider == LLMProvider.ANTHROPIC:
        return [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
            "claude-3-haiku-20240307",
        ]
    elif provider == LLMProvider.GOOGLE:
        return [
            "gemini-3-pro-preview",
            "gemini-3-flash-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
        ]
    else:
        return []


default_model = {
    LLMProvider.OPENAI: "gpt-5-nano",
    LLMProvider.ANTHROPIC: "claude-haiku-4-5-20251001",
    LLMProvider.GOOGLE: "gemini-3.1-flash-lite-preview"
}