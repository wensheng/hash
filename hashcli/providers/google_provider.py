"""Google AI provider implementation for Hash CLI."""

import json
from typing import Any, Dict, List, Optional

import google.generativeai as genai
from google.generativeai.types import HarmBlockThreshold, HarmCategory

from ..config import HashConfig
from ..llm_handler import LLMResponse, ToolCall
from .base import LLMProvider


class GoogleProvider(LLMProvider):
    """Google AI provider implementation using Gemini models."""

    def __init__(self, config: HashConfig):
        super().__init__(config)
        genai.configure(api_key=config.google_api_key)
        self.model_name = config.google_model
        self.model = genai.GenerativeModel(self.model_name)

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """Generate response using Google AI API."""

        try:
            # Convert messages to Google format
            google_messages = self._format_messages_for_provider(messages)

            # Prepare generation config
            generation_config = genai.types.GenerationConfig(
                temperature=0.7,
                max_output_tokens=2000,
            )

            # Prepare safety settings (less restrictive for development tools)
            safety_settings = {
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
            }

            # Start conversation or continue existing one
            if len(google_messages) == 1:
                # Single message
                response = await self.model.generate_content_async(
                    google_messages[0],
                    generation_config=generation_config,
                    safety_settings=safety_settings,
                )
            else:
                # Multi-turn conversation
                chat = self.model.start_chat()
                # Send all but the last message to establish history
                for message in google_messages[:-1]:
                    await chat.send_message_async(message)

                # Send final message and get response
                response = await chat.send_message_async(
                    google_messages[-1],
                    generation_config=generation_config,
                    safety_settings=safety_settings,
                )

            # Extract content
            if response.candidates and len(response.candidates) > 0:
                candidate = response.candidates[0]
                content = ""

                if candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, "text"):
                            content += part.text

                # Note: Google AI doesn't currently support function calling in the same way
                # as OpenAI/Anthropic, so tool calls are not implemented yet
                tool_calls = []

                # Extract usage information
                usage = None
                if hasattr(response, "usage_metadata"):
                    usage = {
                        "prompt_tokens": getattr(
                            response.usage_metadata, "prompt_token_count", 0
                        ),
                        "completion_tokens": getattr(
                            response.usage_metadata, "candidates_token_count", 0
                        ),
                        "total_tokens": getattr(
                            response.usage_metadata, "total_token_count", 0
                        ),
                    }

                return LLMResponse(
                    content=content,
                    tool_calls=tool_calls,
                    model=self.model_name,
                    usage=usage,
                )
            else:
                return LLMResponse(
                    content="No response generated. Content may have been blocked by safety filters.",
                    model=self.model_name,
                )

        except Exception as e:
            error_message = str(e)

            # Handle specific Google AI errors
            if "API_KEY_INVALID" in error_message:
                error_message = (
                    "Invalid Google AI API key. Please check your configuration."
                )
            elif "QUOTA_EXCEEDED" in error_message:
                error_message = "API quota exceeded. Please try again later."
            elif "blocked" in error_message.lower():
                error_message = "Content was blocked by Google's safety filters."

            return LLMResponse(
                content=f"Google AI error: {error_message}", model=self.model_name
            )

    def get_model_name(self) -> str:
        """Get the current model name."""
        return self.model_name

    def validate_configuration(self) -> bool:
        """Validate Google AI configuration."""
        return (
            self.config.google_api_key is not None
            and len(self.config.google_api_key.strip()) > 0
            and self.model_name is not None
        )

    def _format_messages_for_provider(
        self, messages: List[Dict[str, str]]
    ) -> List[str]:
        """Convert OpenAI format messages to Google AI format."""
        google_messages = []
        current_content = ""

        for message in messages:
            role = message["role"]
            content = message["content"]

            if role == "system":
                # Prepend system message as context
                current_content = f"System: {content}\\n\\n"
            elif role == "user":
                if current_content:
                    google_messages.append(current_content + f"User: {content}")
                    current_content = ""
                else:
                    google_messages.append(f"User: {content}")
            elif role == "assistant":
                google_messages.append(f"Assistant: {content}")
            elif role == "tool":
                # Include tool results as context
                current_content += f"Tool Result: {content}\\n\\n"

        # Handle any remaining content
        if current_content:
            if google_messages:
                google_messages[-1] += "\\n\\n" + current_content
            else:
                google_messages.append(current_content)

        return google_messages

    def set_model(self, model: str):
        """Change the model being used."""
        self.model_name = model
        self.config.google_model = model
        self.model = genai.GenerativeModel(self.model_name)
