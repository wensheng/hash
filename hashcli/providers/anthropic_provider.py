"""Anthropic provider implementation for Hash CLI."""

import json
from typing import Any, Callable, Dict, List, Optional

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
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream_handler: Optional[Callable[[str], None]] = None,
    ) -> LLMResponse:
        """Generate response using Anthropic API."""

        try:
            # Convert messages to Anthropic format
            anthropic_messages = self._format_messages_for_provider(messages)

            # Prepare request parameters
            request_params = {
                "model": self.model,
                "messages": anthropic_messages["messages"],
                "max_tokens": self.config.max_response_tokens,
                "temperature": 0.7,
            }

            # Add system message if present
            if anthropic_messages["system"]:
                request_params["system"] = anthropic_messages["system"]

            # Add tools if provided
            if tools:
                request_params["tools"] = self._format_tools_for_provider(tools)

            # Make API call
            streamed_content: List[str] = []
            if self.config.streaming and stream_handler:
                async with self.client.messages.stream(**request_params) as stream:
                    async for text in stream.text_stream:
                        streamed_content.append(text)
                        stream_handler(text)
                    response = await stream.get_final_message()
            else:
                response = await self.client.messages.create(**request_params)

            # Extract response content and tool calls
            content_blocks = response.content
            content = ""
            tool_calls = []

            for block in content_blocks:
                if block.type == "text":
                    content += block.text
                elif block.type == "tool_use":
                    tool_calls.append(ToolCall(name=block.name, arguments=block.input, call_id=block.id))
            if not content and streamed_content:
                content = "".join(streamed_content)

            # Extract usage information
            usage = (
                {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                }
                if response.usage
                else None
            )

            return LLMResponse(content=content, tool_calls=tool_calls, model=self.model, usage=usage)

        except anthropic.RateLimitError:
            return LLMResponse(
                content="Rate limit exceeded. Please try again in a moment.",
                model=self.model,
            )
        except anthropic.AuthenticationError:
            return LLMResponse(
                content="Authentication failed. Please check your Anthropic API key.",
                model=self.model,
            )
        except anthropic.APIError as e:
            return LLMResponse(content=f"Anthropic API error: {str(e)}", model=self.model)
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

    def _format_messages_for_provider(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert OpenAI format messages to Anthropic format."""
        system_message = None
        anthropic_messages = []

        for message in messages:
            role = message["role"]
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])

            if role == "system":
                system_message = content
            elif role == "assistant":
                # Handle assistant messages with tool calls
                if tool_calls:
                    # Convert tool_calls to Anthropic's tool_use content blocks
                    content_blocks = []
                    if content:
                        # Add text content if present
                        content_blocks.append({"type": "text", "text": content})
                    # Add tool_use blocks for each tool call
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id", tc.get("call_id", "")),
                            "name": func.get("name", ""),
                            "input": (
                                json.loads(func["arguments"])
                                if isinstance(func.get("arguments"), str)
                                else func.get("arguments", {})
                            ),
                        })
                    anthropic_messages.append({"role": role, "content": content_blocks})
                elif content:
                    # Regular assistant message with just text
                    anthropic_messages.append({"role": role, "content": content})
            elif role == "user":
                anthropic_messages.append({"role": role, "content": content})
            elif role == "tool":
                # Convert tool result to user message with tool_result content block
                tool_call_id = message.get("tool_call_id", message.get("call_id", ""))
                if tool_call_id:
                    anthropic_messages.append({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": content,
                        }],
                    })
                else:
                    # Fallback for messages without tool_call_id
                    anthropic_messages.append({"role": "user", "content": f"Tool result: {content}"})

        return {"system": system_message, "messages": anthropic_messages}

    def _format_tools_for_provider(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI format tools to Anthropic format."""
        anthropic_tools = []

        for tool in tools:
            if tool["type"] == "function":
                func = tool["function"]
                anthropic_tools.append({
                    "name": func["name"],
                    "description": func["description"],
                    "input_schema": func["parameters"],
                })

        return anthropic_tools

    def set_model(self, model: str):
        """Change the model being used."""
        self.model = model
        self.config.anthropic_model = model

    def get_system_prompt(self) -> str:
        """Get the system prompt for the LLM."""
        return f"""You are Hash, an intelligent terminal assistant designed to help users with command-line tasks, programming, system administration, and general technical questions.

Key capabilities:
- Execute shell commands (with user permission)
- Read and analyze files
- Search the web for current information  
- Provide programming assistance
- Debug and troubleshoot issues
- Explain complex technical concepts

Guidelines:
- Be concise and keep responses under {self.config.max_response_tokens} tokens unless the user explicitly requests more
- Always ask for confirmation before executing potentially destructive commands
- Provide command explanations when helpful
- Suggest alternatives when appropriate
- Prioritize security and best practices
- Indicate when you're unsure and suggest verification steps
- **Prefer simple, single-line commands** (e.g., `seq`, `grep`, `find`) over complex shell loops or scripts. Specifically, use `seq` for number sequences.
- Shell operators `|` and `;` are {'allowed' if self.config.allow_shell_operators else 'disabled'}; only use them when allowed.

Tool usage policy:
- **Action Requests:** If the user asks you to perform an action or retrieve information directly (e.g., "show me disk usage", "list files", "read README.md", "check time"), **CALL THE TOOL DIRECTLY**. Do not ask for confirmation in text; the system handles that.
- **Informational/How-to Requests:** If the user asks *how* to do something (e.g., "how do I check disk usage", "explain ls command"), provide a text explanation. **DO NOT call the tool**. Instead, append a final line exactly: "do you want execute `<command>`?" (where `<command>` is the **full command string with all arguments**, e.g., `ls -la`, wrapped in backticks).
- **Time/Date:** For "what day is today" or "current time", use `execute_shell_command` with `date`.
- **Web Search:** Use the `web_search` tool only when the user explicitly asks to search/browse or requests sources, or when the answer is time-sensitive/likely to change (e.g., current events, prices, schedules). Do **not** use it for general knowledge or explanatory questions (e.g., "why is the sky blue").
- **System Checks:** For local checks (OS, username, directory), use the appropriate tool.
- **Ambiguity:** If unsure whether to execute, err on the side of explaining (text response).

Never include the confirmation line ("do you want execute...") if you are calling a tool. That line is ONLY for text-based suggestions where you did NOT call a tool.

You have access to tools that can interact with the system. Use them appropriately to assist the user effectively."""
