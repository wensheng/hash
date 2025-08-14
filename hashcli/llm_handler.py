"""LLM handler with provider abstraction and tool calling capabilities."""

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union

from rich.console import Console
from rich.prompt import Confirm

from .config import HashConfig, LLMProvider
from .history import ConversationHistory

console = Console()


class ToolCall:
    """Represents a tool call request from the LLM."""

    def __init__(
        self, name: str, arguments: Dict[str, Any], call_id: Optional[str] = None
    ):
        self.name = name
        self.arguments = arguments
        self.call_id = call_id or f"call_{name}_{hash(str(arguments))}"

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

    def __init__(self, config: HashConfig):
        self.config = config
        self.provider = self._get_provider()
        self.history = (
            ConversationHistory(config.history_dir) if config.history_enabled else None
        )
        self.current_session_id = None

    async def chat(self, message: str) -> str:
        """Main chat interface that handles the complete conversation flow."""
        try:
            # Start new session if needed
            if self.history and not self.current_session_id:
                self.current_session_id = self.history.start_session()

            # Add user message to history
            if self.history:
                self.history.add_message(self.current_session_id, "user", message)

            # Get conversation context
            context_messages = self._get_conversation_context()

            # Get LLM response
            response = await self.provider.generate_response(
                messages=context_messages, tools=self._get_available_tools()
            )

            # Handle tool calls if present
            if response.has_tool_calls():
                response = await self._handle_tool_calls(response, context_messages)

            # Add assistant response to history
            if self.history:
                self.history.add_message(
                    self.current_session_id, "assistant", response.content
                )

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
        messages.append({"role": "system", "content": self._get_system_prompt()})

        # Add conversation history
        if self.history and self.current_session_id:
            history_messages = self.history.get_recent_messages(
                self.current_session_id, limit=20  # Keep recent context
            )
            messages.extend(history_messages)

        return messages

    def _get_system_prompt(self) -> str:
        """Get the system prompt for the LLM."""
        return """You are Hash, an intelligent terminal assistant designed to help users with command-line tasks, programming, system administration, and general technical questions.

Key capabilities:
- Execute shell commands (with user permission)
- Read and analyze files
- Search the web for current information  
- Provide programming assistance
- Debug and troubleshoot issues
- Explain complex technical concepts

Guidelines:
- Be concise but thorough in your responses
- Always ask for confirmation before executing potentially destructive commands
- Provide command explanations when helpful
- Suggest alternatives when appropriate
- Prioritize security and best practices
- Indicate when you're unsure and suggest verification steps

You have access to tools that can interact with the system. Use them appropriately to assist the user effectively."""

    def _get_available_tools(self) -> List[Dict[str, Any]]:
        """Get list of available tools for the LLM."""
        tools = []

        # Shell command execution tool
        if self.config.allow_command_execution:
            tools.append(
                {
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
                        },
                    },
                }
            )

        # File system operations
        tools.extend(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read the contents of a text file",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "Path to the file to read",
                                }
                            },
                            "required": ["file_path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "description": "Write content to a text file",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "Path to the file to write",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Content to write to the file",
                                },
                            },
                            "required": ["file_path", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "description": "List contents of a directory",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "directory_path": {
                                    "type": "string",
                                    "description": "Path to the directory to list",
                                },
                                "show_hidden": {
                                    "type": "boolean",
                                    "description": "Whether to show hidden files",
                                    "default": False,
                                },
                            },
                            "required": ["directory_path"],
                        },
                    },
                },
            ]
        )

        return tools

    async def _handle_tool_calls(
        self, response: LLMResponse, context_messages: List[Dict[str, str]]
    ) -> LLMResponse:
        """Handle tool calls from the LLM response."""
        from .tools import get_tool_executor

        tool_results = []

        for tool_call in response.tool_calls:
            # Get user confirmation if required
            if self.config.require_confirmation:
                if not self._get_user_confirmation(tool_call):
                    tool_results.append(
                        {
                            "tool_call_id": tool_call.call_id,
                            "output": "User declined to execute this tool call.",
                        }
                    )
                    continue

            # Execute tool call
            try:
                executor = get_tool_executor(tool_call.name)
                result = await executor.execute(tool_call.arguments, self.config)

                tool_results.append(
                    {"tool_call_id": tool_call.call_id, "output": str(result)}
                )

            except Exception as e:
                tool_results.append(
                    {
                        "tool_call_id": tool_call.call_id,
                        "output": f"Error executing tool: {e}",
                    }
                )

        # Get follow-up response from LLM with tool results
        tool_call_messages = [
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": tc.call_id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
        ]
        tool_result_messages = [
            {
                "role": "tool",
                "content": result["output"],
                "tool_call_id": result["tool_call_id"],
            }
            for result in tool_results
        ]
        messages_with_tools = (
            context_messages + tool_call_messages + tool_result_messages
        )

        follow_up_response = await self.provider.generate_response(messages_with_tools)
        return follow_up_response

    def _get_user_confirmation(self, tool_call: ToolCall) -> bool:
        """Get user confirmation for a tool call."""
        console.print(f"\n[bold yellow]Tool Call Request:[/bold yellow]")
        console.print(f"Function: [cyan]{tool_call.name}[/cyan]")
        console.print(
            f"Arguments: [dim]{json.dumps(tool_call.arguments, indent=2)}[/dim]"
        )

        return Confirm.ask("Execute this tool call?", default=False)

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
