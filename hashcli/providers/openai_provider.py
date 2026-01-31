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
        self.client = AsyncOpenAI(api_key=config.openai_api_key, base_url=config.openai_base_url)
        self.model = config.openai_model

    async def generate_response(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream_handler: Optional[Callable[[str], None]] = None,
    ) -> LLMResponse:
        """Generate response using OpenAI API."""

        try:

            def get_field(obj: Any, name: str, default: Any = None) -> Any:
                if isinstance(obj, dict):
                    return obj.get(name, default)
                return getattr(obj, name, default)

            def extract_text_from_content(content_item: Any) -> Optional[str]:
                content_type = get_field(content_item, "type")
                if content_type in ("output_text", "input_text", "text"):
                    text = get_field(content_item, "text")
                    if text:
                        return text
                if content_type == "refusal":
                    refusal = get_field(content_item, "refusal")
                    if refusal:
                        return refusal
                text = get_field(content_item, "text")
                if text:
                    return text
                content = get_field(content_item, "content")
                if content:
                    return content
                return None

            # Extract system/developer instructions and convert remaining messages.
            instruction_parts: List[str] = []
            input_messages: List[Dict[str, Any]] = []
            for message in messages:
                role = message.get("role")
                if role in ("system", "developer"):
                    content = message.get("content", "")
                    if content:
                        instruction_parts.append(str(content))
                else:
                    input_messages.append(message)

            # Convert chat-style messages (with tool_calls/tool results) to Responses input items.
            # If we somehow lost all input messages, fall back to the full message list.
            input_items = self._format_messages_for_responses(input_messages or messages)
            instructions = "\n\n".join(instruction_parts) if instruction_parts else None

            # Convert tools to Responses API shape if needed
            response_tools = None
            if tools:
                response_tools = []
                for tool in tools:
                    if tool.get("type") == "function" and "function" in tool:
                        function_spec = tool["function"]
                        response_tools.append({
                            "type": "function",
                            "name": function_spec.get("name"),
                            "description": function_spec.get("description"),
                            "parameters": function_spec.get("parameters"),
                            "strict": True,
                        })
                    else:
                        response_tools.append(tool)

            # Prepare request parameters
            text_config: Dict[str, Any] = {"format": {"type": "text"}}
            if self.config.openai_text_verbosity:
                text_config["verbosity"] = self.config.openai_text_verbosity

            request_params = {
                "model": self.model,
                "input": input_items,
                "max_output_tokens": self.config.max_response_tokens,
                "text": text_config,
            }

            # Add tools if provided
            if response_tools:
                request_params["tools"] = response_tools
                request_params["tool_choice"] = "auto"
            if instructions:
                request_params["instructions"] = instructions
            if self.config.openai_reasoning_effort:
                request_params["reasoning"] = {"effort": self.config.openai_reasoning_effort}

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

            response_error = get_field(response, "error")
            if response_error:
                error_code = get_field(response_error, "code", "unknown_error")
                error_message = get_field(response_error, "message", str(response_error))
                return LLMResponse(
                    content=f"OpenAI response error ({error_code}): {error_message}",
                    model=self.model,
                )

            # Extract response content
            content_parts: List[str] = []

            # Extract tool calls if present
            tool_calls = []
            reasoning_summaries: List[str] = []
            for output in get_field(response, "output", []) or []:
                output_type = get_field(output, "type")
                if output_type == "message":
                    message_content = get_field(output, "content", [])
                    if isinstance(message_content, str):
                        if message_content:
                            content_parts.append(message_content)
                    else:
                        for content in message_content or []:
                            text = extract_text_from_content(content)
                            if text:
                                content_parts.append(text)
                elif output_type in ("output_text", "text"):
                    text = get_field(output, "text", "")
                    if text:
                        content_parts.append(text)
                elif output_type == "function_call":
                    try:
                        raw_arguments = get_field(output, "arguments")
                        name = get_field(output, "name")
                        if name is None or raw_arguments is None:
                            continue
                        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                        tool_calls.append(
                            ToolCall(
                                name=name,
                                arguments=arguments,
                                call_id=(get_field(output, "call_id") or get_field(output, "id")),
                            )
                        )
                    except json.JSONDecodeError:
                        content_parts.append(
                            f"\\n\\nNote: Malformed tool call arguments: {get_field(output, 'arguments')}"
                        )
                elif output_type == "custom_tool_call":
                    raw_input = get_field(output, "input")
                    name = get_field(output, "name")
                    if name is None or raw_input is None:
                        continue
                    arguments: Any = raw_input
                    if isinstance(raw_input, str):
                        try:
                            arguments = json.loads(raw_input)
                        except json.JSONDecodeError:
                            arguments = {"input": raw_input}
                    tool_calls.append(
                        ToolCall(
                            name=name,
                            arguments=arguments,
                            call_id=(get_field(output, "call_id") or get_field(output, "id")),
                        )
                    )
                elif output_type == "reasoning":
                    summaries = get_field(output, "summary", []) or []
                    for summary in summaries:
                        summary_text = get_field(summary, "text")
                        if summary_text:
                            reasoning_summaries.append(str(summary_text))

            content = "".join(content_parts)
            if not content and streamed_content:
                content = "".join(streamed_content)
            if not content:
                fallback_text = get_field(response, "output_text")
                if callable(fallback_text):
                    try:
                        fallback_text = fallback_text()
                    except TypeError:
                        pass
                if fallback_text:
                    content = str(fallback_text)
            if not content and not tool_calls and reasoning_summaries:
                content = "Reasoning summary (no final text output): " + " ".join(reasoning_summaries)
            if not content and not tool_calls:
                status = get_field(response, "status")
                incomplete = get_field(response, "incomplete_details")
                detail = ""
                if status and status != "completed":
                    detail = f" (status: {status})"
                if incomplete:
                    detail = f"{detail} (incomplete: {incomplete})"
                content = f"No response generated by OpenAI.{detail} Try another model or run with --debug for details."

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

            return LLMResponse(content=content, tool_calls=tool_calls, model=self.model, usage=usage)

        except openai.RateLimitError:
            return LLMResponse(
                content="Rate limit exceeded. Please try again in a moment.",
                model=self.model,
            )
        except openai.AuthenticationError:
            return LLMResponse(
                content="Authentication failed. Please check your OpenAI API key.",
                model=self.model,
            )
        except openai.APIError as e:
            return LLMResponse(content=f"OpenAI API error: {str(e)}", model=self.model)
        except Exception as e:
            return LLMResponse(content=f"Unexpected error: {str(e)}", model=self.model)

    def _format_messages_for_responses(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI chat-format messages to Responses API input items."""
        input_items: List[Dict[str, Any]] = []

        assistant_msg_index = 0

        def make_message_item(role: str, content: Any) -> Dict[str, Any]:
            """Build a typed message item for the Responses API."""
            nonlocal assistant_msg_index
            if not isinstance(content, (str, list)):
                content = str(content)

            if role == "assistant":
                assistant_msg_index += 1
                if isinstance(content, list):
                    content_parts = content
                else:
                    content_parts = [{"type": "output_text", "text": content}]
                return {
                    "type": "message",
                    "role": "assistant",
                    "content": content_parts,
                    "status": "completed",
                    "id": f"assistant_msg_{assistant_msg_index}",
                }

            if isinstance(content, list):
                content_parts = content
            else:
                content_parts = [{"type": "input_text", "text": content}]

            return {"type": "message", "role": role, "content": content_parts}

        for message in messages:
            role = message.get("role")
            content = message.get("content", "")

            # Map standard roles to easy input messages.
            if role in ("system", "user", "assistant", "developer"):
                if content:
                    input_items.append(make_message_item(role, content))

            # Map assistant tool calls to function_call items.
            tool_calls = message.get("tool_calls") or []
            for tool_call in tool_calls:
                func = tool_call.get("function", {})
                call_id = tool_call.get("id") or tool_call.get("call_id")
                name = func.get("name")
                arguments = func.get("arguments")
                if call_id and name and arguments is not None:
                    input_items.append({
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                    })

            # Map tool outputs to function_call_output items.
            if role == "tool":
                call_id = message.get("tool_call_id") or message.get("call_id")
                if call_id:
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": content or "",
                    })
                elif content:
                    # Fallback to preserve tool output if call_id is missing.
                    input_items.append(make_message_item("user", f"Tool result: {content}"))

        return input_items

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
