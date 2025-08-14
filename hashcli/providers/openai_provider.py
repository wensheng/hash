"""OpenAI provider implementation for Hash CLI."""

import json
from typing import Any, Dict, List, Optional

import openai
from openai import AsyncOpenAI

from ..config import HashConfig
from ..llm_handler import LLMResponse, ToolCall
from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI provider implementation using GPT models."""

    def __init__(self, config: HashConfig):
        super().__init__(config)
        self.client = AsyncOpenAI(api_key=config.openai_api_key)
        self.model = config.openai_model

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """Generate response using OpenAI API."""

        try:
            # Prepare request parameters
            request_params = {
                "model": self.model,
                "messages": messages,
                "max_completion_tokens": 4096,
            }

            # Add tools if provided
            if tools:
                request_params["tools"] = tools
                request_params["tool_choice"] = "auto"

            # Make API call
            response = await self.client.chat.completions.create(**request_params)

            # Extract response content
            message = response.choices[0].message
            content = message.content or ""

            # Extract tool calls if present
            tool_calls = []
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    try:
                        arguments = json.loads(tool_call.function.arguments)
                        tool_calls.append(
                            ToolCall(
                                name=tool_call.function.name,
                                arguments=arguments,
                                call_id=tool_call.id,
                            )
                        )
                    except json.JSONDecodeError:
                        # Handle malformed JSON in tool calls
                        content += f"\\n\\nNote: Malformed tool call arguments: {tool_call.function.arguments}"

            # Extract usage information
            usage = (
                {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                if response.usage
                else None
            )

            return LLMResponse(
                content=content, tool_calls=tool_calls, model=self.model, usage=usage
            )

        except openai.RateLimitError as e:
            return LLMResponse(
                content="Rate limit exceeded. Please try again in a moment.",
                model=self.model,
            )
        except openai.AuthenticationError as e:
            return LLMResponse(
                content="Authentication failed. Please check your OpenAI API key.",
                model=self.model,
            )
        except openai.APIError as e:
            return LLMResponse(content=f"OpenAI API error: {str(e)}", model=self.model)
        except Exception as e:
            return LLMResponse(content=f"Unexpected error: {str(e)}", model=self.model)

    def get_model_name(self) -> str:
        """Get the current model name."""
        return self.model

    def validate_configuration(self) -> bool:
        """Validate OpenAI configuration."""
        return (
            self.config.openai_api_key is not None
            and len(self.config.openai_api_key.strip()) > 0
            and self.model is not None
        )

    def set_model(self, model: str):
        """Change the model being used."""
        self.model = model
        self.config.openai_model = model
