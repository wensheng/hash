"""LLM handler with provider abstraction and tool calling capabilities."""

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

from rich.prompt import Confirm

from .config import HashConfig, LLMProvider
from .history import ConversationHistory
from .ui import console


class ToolCall:
    """Represents a tool call request from the LLM."""

    def __init__(
        self,
        name: str,
        arguments: Dict[str, Any],
        call_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.arguments = arguments
        self.call_id = call_id or f"call_{name}_{hash(str(arguments))}"
        self.metadata = metadata or {}

    def __repr__(self):
        return f"ToolCall(name='{self.name}', arguments={self.arguments})"


class LLMResponse:
    """Represents a response from an LLM provider."""

    def __init__(
        self,
        content: str,
        tool_calls: Optional[List[ToolCall]] = None,
        model: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
    ):
        self.content = content
        self.tool_calls = tool_calls or []
        self.model = model
        self.usage = usage

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMHandler:
    """Main LLM handler that manages providers and tool calling."""

    def __init__(self, config: HashConfig, session_id: Optional[str] = None):
        self.config = config
        self.provider = self._get_provider()
        self.history = ConversationHistory(config.history_dir) if config.history_enabled else None
        self.current_session_id = session_id or os.environ.get("HASHCLI_SESSION_ID")
        self.last_tool_calls_executed = False

    async def chat(
        self,
        message: str,
        stream_handler: Optional[Callable[[str], None]] = None,
        force_tool_confirmation: Optional[bool] = None,
    ) -> str:
        """Main chat interface that handles the complete conversation flow."""
        try:
            self.last_tool_calls_executed = False
            # Start new session if needed
            if self.history:
                if not self.current_session_id:
                    self.current_session_id = self.history.start_session()
                elif self.history.get_session_info(self.current_session_id) is None:
                    self.history.start_session(session_id=self.current_session_id)

            # Add user message to history
            if self.history:
                self.history.add_message(self.current_session_id, "user", message)

            # Get conversation context
            context_messages = self._get_conversation_context()
            available_tools = self._get_available_tools(message)

            # Get LLM response
            response = await self.provider.generate_response(
                messages=context_messages,
                tools=available_tools,
                stream_handler=stream_handler if self.config.streaming else None,
            )

            # Handle tool calls if present
            if response.has_tool_calls():
                self.last_tool_calls_executed = True
                response = await self._handle_tool_calls(
                    response,
                    context_messages,
                    available_tools,
                    stream_handler,
                    force_tool_confirmation=force_tool_confirmation,
                )

            # Add assistant response to history
            if self.history:
                self.history.add_message(self.current_session_id, "assistant", response.content)

            return response.content

        except Exception as e:
            error_msg = f"LLM Error: {str(e)}"
            if self.config.show_debug:
                import traceback

                error_msg += f"\nDebug info: {traceback.format_exc()}"
            return error_msg

    def _get_provider(self):
        """Get the appropriate LLM provider based on configuration."""
        from .providers import AnthropicProvider, GoogleProvider, OpenAIProvider

        if self.config.llm_provider == LLMProvider.OPENAI:
            return OpenAIProvider(self.config)
        elif self.config.llm_provider == LLMProvider.ANTHROPIC:
            return AnthropicProvider(self.config)
        elif self.config.llm_provider == LLMProvider.GOOGLE:
            return GoogleProvider(self.config)
        else:
            raise ValueError(f"Unknown provider: {self.config.llm_provider}")

    def _get_conversation_context(self) -> List[Dict[str, str]]:
        """Get conversation context for the LLM."""
        messages = []

        # Add system message
        messages.append({"role": "system", "content": self.provider.get_system_prompt()})

        # Add conversation history
        if self.history and self.current_session_id:
            history_messages = self.history.get_recent_messages(
                self.current_session_id, limit=20  # Keep recent context
            )
            messages.extend(history_messages)

        return messages

    def _get_available_tools(self, user_message: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get list of available tools for the LLM."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "lookup_tldr_command",
                    "description": (
                        "Look up concise tldr examples and usage notes for a shell command. "
                        "Use this when answering command-specific questions and you need grounded syntax or examples."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The command to look up, such as tar, find, or xargs.",
                            },
                            "platform": {
                                "type": ["string", "null"],
                                "description": "Optional platform override such as linux, osx, or windows.",
                            },
                            "language": {
                                "type": ["string", "null"],
                                "description": "Optional language override for tldr lookup.",
                            },
                            "search": {
                                "type": "boolean",
                                "description": "Set to true to search commands by keyword instead of looking up one exact command.",
                            },
                        },
                        "required": ["command", "platform", "language", "search"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

        if self.config.allow_command_execution and self._should_expose_shell_tool(user_message):
            tools.append({
                "type": "function",
                "function": {
                    "name": "execute_shell_command",
                    "description": "Execute a shell command and return its output. Use with caution.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The shell command to execute",
                            },
                            "description": {
                                "type": "string",
                                "description": "Brief description of what this command does",
                            },
                        },
                        "required": ["command", "description"],
                        "additionalProperties": False,
                    },
                },
            })

        return tools

    def _should_expose_shell_tool(self, user_message: Optional[str]) -> bool:
        """Hide direct execution for explanatory command-help queries."""
        if not user_message:
            return True

        normalized = user_message.strip().lower()
        if not normalized:
            return True

        explanatory_patterns = (
            r"^(how to|how do i|how can i|what command|which command)\b",
            r"^(explain|what does|how does)\b",
        )
        return not any(re.match(pattern, normalized) for pattern in explanatory_patterns)

    async def _handle_tool_calls(
        self,
        response: LLMResponse,
        context_messages: List[Dict[str, str]],
        available_tools: List[Dict[str, Any]],
        stream_handler: Optional[Callable[[str], None]] = None,
        force_tool_confirmation: Optional[bool] = None,
    ) -> LLMResponse:
        """Handle tool calls from the LLM response."""
        from .tools import get_tool_executor

        messages_with_tools = list(context_messages)
        current_response = response
        max_tool_rounds = 3
        last_tool_results: List[Dict[str, Any]] = []

        for _ in range(max_tool_rounds):
            if not current_response.has_tool_calls():
                return self._maybe_fallback_to_tool_output(current_response, last_tool_results)

            tool_results = []
            for tool_call in current_response.tool_calls:
                executor = get_tool_executor(tool_call.name)
                if executor is None:
                    tool_results.append({
                        "tool_call_id": tool_call.call_id,
                        "tool_name": tool_call.name,
                        "output": f"Unknown tool: {tool_call.name}",
                    })
                    continue

                # Get user confirmation if required
                effective_confirmation = self.config.require_confirmation
                if force_tool_confirmation is not None:
                    effective_confirmation = force_tool_confirmation

                if self._should_confirm_tool_call(executor, tool_call, effective_confirmation):
                    if not self._get_user_confirmation(tool_call):
                        tool_results.append({
                            "tool_call_id": tool_call.call_id,
                            "tool_name": tool_call.name,
                            "output": "User declined to execute this tool call.",
                        })
                        continue

                # Execute tool call
                try:
                    result = await executor.execute(tool_call.arguments, self.config)
                    tool_results.append({
                        "tool_call_id": tool_call.call_id,
                        "tool_name": tool_call.name,
                        "output": str(result),
                    })
                except Exception as e:
                    tool_results.append({
                        "tool_call_id": tool_call.call_id,
                        "tool_name": tool_call.name,
                        "output": f"Error executing tool: {e}",
                    })

            # Get follow-up response from LLM with tool results
            # Note: Some providers (like Anthropic) don't allow empty content in assistant messages
            # with tool calls. Only add content if it's non-empty.
            assistant_msg = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": tc.call_id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                        **tc.metadata,
                    }
                    for tc in current_response.tool_calls
                ],
            }
            if current_response.content:
                assistant_msg["content"] = current_response.content

            tool_call_messages = [assistant_msg]
            tool_result_messages = [
                {
                    "role": "tool",
                    "content": result["output"],
                    "tool_call_id": result["tool_call_id"],
                }
                for result in tool_results
            ]
            messages_with_tools.extend(tool_call_messages + tool_result_messages)
            last_tool_results = tool_results

            current_response = await self.provider.generate_response(
                messages_with_tools,
                tools=available_tools,
                stream_handler=stream_handler if self.config.streaming else None,
            )

        return self._maybe_fallback_to_tool_output(current_response, last_tool_results, reached_round_limit=True)

    def _maybe_fallback_to_tool_output(
        self,
        response: LLMResponse,
        tool_results: List[Dict[str, Any]],
        reached_round_limit: bool = False,
    ) -> LLMResponse:
        """Use tool output directly when the model returns no final text."""
        if response.content and response.content.strip():
            return response
        if not tool_results:
            return response

        formatted = self._format_tool_results(tool_results)
        if reached_round_limit:
            response.content = (
                "No final response from the model after tool calls. Showing the latest tool output instead.\n\n"
                + formatted
            )
        else:
            response.content = formatted
        return response

    def _format_tool_results(self, tool_results: List[Dict[str, Any]]) -> str:
        """Format tool results for user display."""
        chunks = []
        for result in tool_results:
            name = result.get("tool_name") or "tool"
            output = result.get("output") or "No output."
            chunks.append(f"Tool result ({name}):\n{output}")
        return "\n\n".join(chunks)

    def _should_confirm_tool_call(
        self,
        executor: Any,
        tool_call: ToolCall,
        effective_confirmation: bool,
    ) -> bool:
        """Apply config-level confirmation plus hard overrides for destructive shell actions."""
        if effective_confirmation and executor.requires_confirmation():
            return True

        if tool_call.name != "execute_shell_command":
            return False

        from .tools.shell import ShellTool

        command = tool_call.arguments.get("command")
        return ShellTool.is_potentially_destructive_command(command)

    def _get_user_confirmation(self, tool_call: ToolCall) -> bool:
        """Get user confirmation for a tool call."""
        console.print("\n[bold yellow]Tool Call Request:[/bold yellow]")
        console.print(f"Function: [cyan]{tool_call.name}[/cyan]")
        console.print(f"Arguments: [dim]{json.dumps(tool_call.arguments, indent=2)}[/dim]")

        return Confirm.ask("Execute this tool call?", default=False, console=console)

    def clear_session(self):
        """Clear the current conversation session."""
        if self.history and self.current_session_id:
            self.history.end_session(self.current_session_id)
        self.current_session_id = None

    def get_session_info(self) -> Optional[Dict[str, Any]]:
        """Get information about the current session."""
        if not self.history or not self.current_session_id:
            return None

        return self.history.get_session_info(self.current_session_id)
