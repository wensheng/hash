"""OpenAI provider implementation for Hash CLI."""

import json
from typing import Any, Callable, Dict, List, Optional

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
        stream_handler: Optional[Callable[[str], None]] = None,
    ) -> LLMResponse:
        """Generate response using OpenAI API."""

        try:
            # Convert tools to Responses API shape if needed
            response_tools = None
            if tools:
                response_tools = []
                for tool in tools:
                    if tool.get("type") == "function" and "function" in tool:
                        function_spec = tool["function"]
                        response_tools.append(
                            {
                                "type": "function",
                                "name": function_spec.get("name"),
                                "description": function_spec.get("description"),
                                "parameters": function_spec.get("parameters"),
                            }
                        )
                    else:
                        response_tools.append(tool)

            # Prepare request parameters
            request_params = {
                "model": self.model,
                "input": messages,
                "max_output_tokens": self.config.max_response_tokens,
            }

            # Add tools if provided
            if response_tools:
                request_params["tools"] = response_tools
                request_params["tool_choice"] = "auto"

            # Make API call
            streamed_content: List[str] = []
            if self.config.streaming and stream_handler:
                async with self.client.responses.stream(**request_params) as stream:
                    async for event in stream:
                        event_type = getattr(event, "type", None)
                        if event_type in (
                            "response.output_text.delta",
                            "response.refusal.delta",
                        ):
                            delta = getattr(event, "delta", None)
                            if delta:
                                streamed_content.append(delta)
                                stream_handler(delta)
                    response = await stream.get_final_response()
            else:
                response = await self.client.responses.create(**request_params)

            # Extract response content
            content_parts: List[str] = []

            # Extract tool calls if present
            tool_calls = []
            for output in response.output or []:
                output_type = getattr(output, "type", None)
                if output_type == "message":
                    for content in output.content:
                        if content.type == "output_text":
                            content_parts.append(content.text)
                        elif content.type == "refusal":
                            content_parts.append(content.refusal)
                elif output_type == "function_call":
                    try:
                        arguments = json.loads(output.arguments)
                        tool_calls.append(
                            ToolCall(
                                name=output.name,
                                arguments=arguments,
                                call_id=output.call_id,
                            )
                        )
                    except json.JSONDecodeError:
                        content_parts.append(
                            "\\n\\nNote: Malformed tool call arguments:"
                            f" {output.arguments}"
                        )

            content = "".join(content_parts)
            if not content and streamed_content:
                content = "".join(streamed_content)

            # Extract usage information
            usage = (
                {
                    "prompt_tokens": response.usage.input_tokens,
                    "completion_tokens": response.usage.output_tokens,
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
