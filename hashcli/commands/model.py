"""Model command implementation for switching LLM models."""

from typing import List

from ..command_proxy import Command
from ..config import HashConfig, LLMProvider, get_model_options


class ModelCommand(Command):
    """Command to switch between LLM models and providers."""

    def execute(self, args: List[str], config: HashConfig) -> str:
        """Switch LLM model or provider."""

        if not args:
            # Show current configuration
            return self._show_current_config(config)

        command = args[0].lower()

        if command == "list":
            return self._list_available_models()
        elif command == "set":
            if len(args) < 2:
                return "Usage: /model set <model_name>"
            return self._set_model(args[1], config)
        elif command == "provider":
            if len(args) < 2:
                return "Usage: /model provider <provider_name>"
            return self._set_provider(args[1], config)
        else:
            # Assume the argument is a model name
            return self._set_model(command, config)

    def _show_current_config(self, config: HashConfig) -> str:
        """Show current model configuration."""
        output = f"Current Configuration:\\n"
        output += f"  Provider: {config.llm_provider.value}\\n"
        output += f"  Model: {config.get_current_model()}\\n"
        output += (
            f"  API Key: {'✓ Set' if config.get_current_api_key() else '✗ Not set'}\\n"
        )
        output += f"\\nUse '/model list' to see available models"
        return output

    def _list_available_models(self) -> str:
        """List all available models by provider."""
        output = "Available Models:\\n\\n"

        for provider in LLMProvider:
            models = get_model_options(provider)
            output += f"{provider.value.upper()}:\\n"
            for model in models:
                output += f"  - {model}\\n"
            output += "\\n"

        output += "Usage:\\n"
        output += "  /model set <model_name>    - Switch to specific model\\n"
        output += "  /model provider <name>     - Switch provider\\n"

        return output.strip()

    def _set_model(self, model_name: str, config: HashConfig) -> str:
        """Set a specific model."""
        model_name = model_name.strip()

        # Determine provider based on model name
        target_provider = None

        # Check OpenAI models
        if any(model_name in model for model in get_model_options(LLMProvider.OPENAI)):
            target_provider = LLMProvider.OPENAI
        # Check Anthropic models
        elif any(
            model_name in model for model in get_model_options(LLMProvider.ANTHROPIC)
        ):
            target_provider = LLMProvider.ANTHROPIC
        # Check Google models
        elif any(
            model_name in model for model in get_model_options(LLMProvider.GOOGLE)
        ):
            target_provider = LLMProvider.GOOGLE
        else:
            # Try to infer from model name patterns
            if "gpt" in model_name.lower() or "openai" in model_name.lower():
                target_provider = LLMProvider.OPENAI
            elif "claude" in model_name.lower():
                target_provider = LLMProvider.ANTHROPIC
            elif "gemini" in model_name.lower():
                target_provider = LLMProvider.GOOGLE

        if not target_provider:
            return f"Unknown model: {model_name}\\nUse '/model list' to see available models"

        # Update configuration
        config.llm_provider = target_provider

        if target_provider == LLMProvider.OPENAI:
            config.openai_model = model_name
        elif target_provider == LLMProvider.ANTHROPIC:
            config.anthropic_model = model_name
        elif target_provider == LLMProvider.GOOGLE:
            config.google_model = model_name

        # Validate API key exists
        if not config.get_current_api_key():
            return f"Model set to {model_name} ({target_provider.value}), but no API key configured.\\nSet HASHCMD_{target_provider.value.upper()}_API_KEY environment variable."

        return f"Switched to {model_name} ({target_provider.value})"

    def _set_provider(self, provider_name: str, config: HashConfig) -> str:
        """Set a specific provider."""
        provider_name = provider_name.lower().strip()

        try:
            target_provider = LLMProvider(provider_name)
        except ValueError:
            return f"Unknown provider: {provider_name}\\nAvailable: openai, anthropic, google"

        # Update configuration
        config.llm_provider = target_provider

        # Validate API key exists
        if not config.get_current_api_key():
            return f"Switched to {target_provider.value}, but no API key configured.\\nSet HASHCMD_{target_provider.value.upper()}_API_KEY environment variable."

        current_model = config.get_current_model()
        return f"Switched to {target_provider.value} (model: {current_model})"

    def get_help(self) -> str:
        """Get help text for the model command."""
        return """Manage LLM models and providers:
  /model                   - Show current configuration
  /model list              - List all available models
  /model set <model>       - Switch to specific model
  /model provider <name>   - Switch provider
  
Examples:
  /model                   - Show current setup
  /model list              - See all options
  /model set gpt-5-mini    - Switch to GPT-5-mini
  /model provider anthropic - Switch to Anthropic
  /model claude-3-sonnet   - Switch to Claude Sonnet"""
