"""Anthropic provider implementation for Hash CLI."""

import json
from typing import Any, Dict, List, Optional

import anthropic
from anthropic import AsyncAnthropic

from ..config import HashConfig
from ..llm_handler import LLMResponse, ToolCall
from .base import LLMProvider


class AnthropicProvider(LLMProvider):
    """Anthropic provider implementation using Claude models."""

    def __init__(self, config: HashConfig):
        super().__init__(config)
        self.client = AsyncAnthropic(api_key=config.anthropic_api_key)
        self.model = config.anthropic_model

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """Generate response using Anthropic API."""

        try:
            # Convert messages to Anthropic format
            anthropic_messages = self._format_messages_for_provider(messages)

            # Prepare request parameters
            request_params = {
                "model": self.model,
                "messages": anthropic_messages["messages"],
                "max_tokens": 2000,
                "temperature": 0.7,
            }

            # Add system message if present
            if anthropic_messages["system"]:
                request_params["system"] = anthropic_messages["system"]

            # Add tools if provided
            if tools:
                request_params["tools"] = self._format_tools_for_provider(tools)

            # Make API call
            response = await self.client.messages.create(**request_params)

            # Extract response content and tool calls
            content_blocks = response.content
            content = ""
            tool_calls = []

            for block in content_blocks:
                if block.type == "text":
                    content += block.text
                elif block.type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            name=block.name, arguments=block.input, call_id=block.id
                        )
                    )

            # Extract usage information
            usage = (
                {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens
                    + response.usage.output_tokens,
                }
                if response.usage
                else None
            )

            return LLMResponse(
                content=content, tool_calls=tool_calls, model=self.model, usage=usage
            )

        except anthropic.RateLimitError as e:
            return LLMResponse(
                content="Rate limit exceeded. Please try again in a moment.",
                model=self.model,
            )
        except anthropic.AuthenticationError as e:
            return LLMResponse(
                content="Authentication failed. Please check your Anthropic API key.",
                model=self.model,
            )
        except anthropic.APIError as e:
            return LLMResponse(
                content=f"Anthropic API error: {str(e)}", model=self.model
            )
        except Exception as e:
            return LLMResponse(content=f"Unexpected error: {str(e)}", model=self.model)

    def get_model_name(self) -> str:
        """Get the current model name."""
        return self.model

    def validate_configuration(self) -> bool:
        """Validate Anthropic configuration."""
        return (
            self.config.anthropic_api_key is not None
            and len(self.config.anthropic_api_key.strip()) > 0
            and self.model is not None
        )

    def _format_messages_for_provider(
        self, messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """Convert OpenAI format messages to Anthropic format."""
        system_message = None
        anthropic_messages = []

        for message in messages:
            role = message["role"]
            content = message["content"]

            if role == "system":
                system_message = content
            elif role in ["user", "assistant"]:
                anthropic_messages.append({"role": role, "content": content})
            elif role == "tool":
                # Convert tool result to user message for Anthropic
                anthropic_messages.append(
                    {"role": "user", "content": f"Tool result: {content}"}
                )

        return {"system": system_message, "messages": anthropic_messages}

    def _format_tools_for_provider(
        self, tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert OpenAI format tools to Anthropic format."""
        anthropic_tools = []

        for tool in tools:
            if tool["type"] == "function":
                func = tool["function"]
                anthropic_tools.append(
                    {
                        "name": func["name"],
                        "description": func["description"],
                        "input_schema": func["parameters"],
                    }
                )

        return anthropic_tools

    def set_model(self, model: str):
        """Change the model being used."""
        self.model = model
        self.config.anthropic_model = model
