"""Google AI provider implementation for Hash CLI."""

from typing import Any, Callable, Dict, List, Optional

from google import genai
from google.genai import types

from ..config import HashConfig
from ..llm_handler import LLMResponse, ToolCall
from .base import LLMProvider


class GoogleProvider(LLMProvider):
    """Google AI provider implementation using Gemini models."""

    def __init__(self, config: HashConfig):
        super().__init__(config)
        self.client = genai.Client(api_key=config.google_api_key)
        self.model_name = config.google_model

    async def generate_response(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream_handler: Optional[Callable[[str], None]] = None,
    ) -> LLMResponse:
        """Generate response using Google AI API."""

        try:
            # Convert messages to Google format
            google_messages = self._format_messages_for_provider(messages)

            # Convert tools to Google format
            google_tools = None
            if tools:
                google_tools = self._convert_tools_to_google_format(tools)

            # Prepare generation config
            config = types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=self.config.max_response_tokens,
                tools=google_tools,
                safety_settings=[
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    ),
                ],
            )

            # Generate content
            # The new SDK handles chat history via contents list nicely
            streamed_content: List[str] = []
            response = None
            if self.config.streaming and stream_handler:
                async for chunk in self.client.aio.models.generate_content_stream(
                    model=self.model_name,
                    contents=google_messages,
                    config=config,
                ):
                    response = chunk
                    chunk_text = getattr(chunk, "text", None)
                    if chunk_text:
                        streamed_content.append(chunk_text)
                        stream_handler(chunk_text)
            else:
                response = await self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=google_messages,
                    config=config,
                )

            # Extract content
            tool_calls: List[Any] = []

            # If streaming, we might have partial text but usually tool calls come in the final response object too
            # However, for simplicity, we focus on the final response object for tool calls.

            content = "".join(streamed_content)

            if response and response.candidates and len(response.candidates) > 0:
                candidate = response.candidates[0]

                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.text and not content:  # Avoid duplicating if already streamed
                            content += part.text

                        if part.function_call:
                            # Extract tool call
                            tool_calls.append(
                                ToolCall(
                                    name=part.function_call.name,
                                    arguments=part.function_call.args,
                                    call_id=f"call_{part.function_call.name}_{hash(str(part.function_call.args))}",
                                )
                            )

                # Extract usage information
                usage = None
                if response.usage_metadata:
                    usage = {
                        "prompt_tokens": response.usage_metadata.prompt_token_count or 0,
                        "completion_tokens": response.usage_metadata.candidates_token_count or 0,
                        "total_tokens": response.usage_metadata.total_token_count or 0,
                    }

                return LLMResponse(
                    content=content,
                    tool_calls=tool_calls,
                    model=self.model_name,
                    usage=usage,
                )

            return LLMResponse(
                content="No response generated. Content may have been blocked by safety filters.",
                model=self.model_name,
            )

        except Exception as e:
            error_message = str(e)

            # Handle specific Google AI errors - adapting strings as they might have changed
            if "API_KEY_INVALID" in error_message:
                error_message = "Invalid Google AI API key. Please check your configuration."
            elif "429" in error_message or "quota" in error_message.lower():
                error_message = "API quota exceeded. Please try again later."
            elif "blocked" in error_message.lower():
                error_message = "Content was blocked by Google's safety filters."

            return LLMResponse(content=f"Google AI error: {error_message}", model=self.model_name)

    def _convert_tools_to_google_format(self, tools: List[Dict[str, Any]]) -> List[types.Tool]:
        """Convert OpenAI-style tools to Google AI format."""
        google_tools = []

        # Google expects a list of Tool objects, where each Tool contains function_declarations

        function_declarations = []

        for tool in tools:
            if tool.get("type") == "function":
                function = tool.get("function", {})
                name = function.get("name")
                description = function.get("description")
                parameters = function.get("parameters")

                # Clean parameters schema to remove unsupported fields like additionalProperties
                cleaned_parameters = self._clean_parameter_schema(parameters)

                function_declarations.append(
                    types.FunctionDeclaration(name=name, description=description, parameters=cleaned_parameters)
                )

        if function_declarations:
            google_tools.append(types.Tool(function_declarations=function_declarations))

        return google_tools

    def _clean_parameter_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively remove unsupported fields from parameter schema."""
        if not isinstance(schema, dict):
            return schema

        cleaned = {}
        for key, value in schema.items():
            # Google API doesn't support additionalProperties in the schema
            if key in ("additionalProperties", "additional_properties"):
                continue

            if isinstance(value, dict):
                cleaned[key] = self._clean_parameter_schema(value)
            elif isinstance(value, list):
                cleaned[key] = [
                    self._clean_parameter_schema(item) if isinstance(item, dict) else item for item in value
                ]
            else:
                cleaned[key] = value

        return cleaned

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

    def _format_messages_for_provider(self, messages: List[Dict[str, str]]) -> List[types.Content]:
        """Convert OpenAI format messages to Google AI format."""
        contents = []

        for message in messages:
            role = message["role"]
            content = message["content"]

            if role == "system":
                # In strict chat structure, system messages might need to be handled differently
                # depending on how we want to use them. The new SDK supports system_instruction
                # in GenerateContentConfig, but here we are mapping a list of messages.
                # Common pattern: Prepend to first user message or separate.
                # However, Gemini 1.5 allows 'system' role or system_instruction.
                # Let's try to map to 'user'/'model' roles as 'system' often clashes in pure chat history
                # unless using system_instruction.
                # For compatibility with the previous logic which merged it:

                # The previous implementation prepended "System: " to user messages.
                # We can replicate a similar behavior or use proper parts.

                parts = [types.Part(text=f"System: {content}")]
                contents.append(types.Content(role="user", parts=parts))
                # Add an acknowledgment to simulate system processing if needed,
                # or just append to the next user message if we could looking ahead.
                # To be safe and simple: just send it as a user message.

            elif role == "user":
                parts = [types.Part(text=content)]
                contents.append(types.Content(role="user", parts=parts))

            elif role == "assistant":
                parts = [types.Part(text=content)]
                contents.append(types.Content(role="model", parts=parts))

            elif role == "tool":
                # Tool results - not fully implemented in this basic migration but
                # mapping to 'user' with context explanation
                parts = [types.Part(text=f"Tool Result: {content}")]
                contents.append(types.Content(role="user", parts=parts))

        # Consolidate adjacent messages of the same role if necessary?
        # The API usually requires alternating User/Model.
        # If we have multiple User messages (e.g. System + User), we might need to merge them.

        merged_contents = []
        if not contents:
            return []

        current_content = contents[0]

        for next_content in contents[1:]:
            if next_content.role == current_content.role:
                # Merge parts
                current_content.parts.extend(next_content.parts)
            else:
                merged_contents.append(current_content)
                current_content = next_content

        merged_contents.append(current_content)

        return merged_contents

    def set_model(self, model: str):
        """Change the model being used."""
        self.model_name = model
        self.config.google_model = model
