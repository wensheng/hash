"""Base LLM provider interface and common functionality."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ..config import HashConfig
from ..llm_handler import LLMResponse, ToolCall


class LLMProvider(ABC):
    """Abstract base class for all LLM providers."""

    def __init__(self, config: HashConfig):
        self.config = config

    @abstractmethod
    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """Generate a response from the LLM provider.

        Args:
            messages: List of conversation messages in OpenAI format
            tools: Optional list of available tools for the LLM to call

        Returns:
            LLMResponse object containing the response and any tool calls
        """
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Get the current model name being used."""
        pass

    @abstractmethod
    def validate_configuration(self) -> bool:
        """Validate that the provider is properly configured."""
        pass

    def _extract_tool_calls(self, response_data: Any) -> List[ToolCall]:
        """Extract tool calls from provider response. Override as needed."""
        return []

    def _format_messages_for_provider(self, messages: List[Dict[str, str]]) -> Any:
        """Format messages for the specific provider. Override as needed."""
        return messages

    def _format_tools_for_provider(self, tools: List[Dict[str, Any]]) -> Any:
        """Format tools for the specific provider. Override as needed."""
        return tools
