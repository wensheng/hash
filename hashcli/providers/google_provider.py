import json
import re
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
            disable_tool_calls = self._should_disable_tool_calls(messages, tools)

            # Prepare messages and tools
            if self.is_gemma_model and tools:
                if disable_tool_calls:
                    google_messages = self._format_messages_for_provider(messages)
                    google_tools = None
                else:
                    # Inject tools into system prompt for Gemma
                    tool_prompt = self._format_tools_for_system_prompt(tools)
                    # Work on a copy of messages to avoid modifying the original list
                    messages_copy = [m.copy() for m in messages]

                    found_system = False
                    for msg in messages_copy:
                        if msg.get("role") == "system":
                            msg["content"] += tool_prompt
                            found_system = True
                            break

                    if not found_system:
                        messages_copy.insert(0, {"role": "system", "content": tool_prompt})

                    google_messages = self._format_messages_for_provider(messages_copy)
                    google_tools = None
            else:
                google_messages = self._format_messages_for_provider(messages)
                google_tools = None
                if tools:
                    google_tools = self._convert_tools_to_google_format(tools)

            tool_config = None
            if disable_tool_calls and google_tools:
                tool_config = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.NONE)
                )

            # Prepare generation config
            config = types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=self.config.max_response_tokens,
                tools=google_tools,
                tool_config=tool_config,
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
                        if part.text:
                            if not content:  # Avoid duplicating if already streamed
                                content += part.text

                            # For Gemma, parse tool calls from text
                            if self.is_gemma_model:
                                text_tool_calls = self._parse_tool_calls_from_text(part.text)
                                if text_tool_calls:
                                    tool_calls.extend(text_tool_calls)

                        if part.function_call:
                            # Extract tool call
                            function_call_id = getattr(part.function_call, "id", None)
                            tool_calls.append(
                                ToolCall(
                                    name=part.function_call.name,
                                    arguments=part.function_call.args,
                                    call_id=(
                                        function_call_id
                                        or f"call_{part.function_call.name}_{hash(str(part.function_call.args))}"
                                    ),
                                    metadata={
                                        key: value
                                        for key, value in {
                                            "thought": getattr(part, "thought", None),
                                            "thought_signature": getattr(part, "thought_signature", None),
                                        }.items()
                                        if value is not None
                                    },
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
                error_message = 'Invalid Google AI API key. Please check your configuration. Run "hi --config" to set this up interactively.'
            elif "429" in error_message or "quota" in error_message.lower():
                error_message = (
                    "Google API quota or rate limit exceeded. Please try again later or choose a different model."
                )
            elif "model" in error_message.lower() or "404" in error_message:
                error_message = f'{error_message} Check the configured Google model or run "hi --config".'
            elif "blocked" in error_message.lower():
                error_message = "Content was blocked by Google's safety filters."

            return LLMResponse(content=f"Google AI error: {error_message}", model=self.model_name)

    def _should_disable_tool_calls(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """Disable tool calling for how-to prompts unless tldr lookup is the only tool."""
        latest_user_message = self._get_latest_user_message(messages)
        if not latest_user_message:
            return False

        normalized = latest_user_message.strip().lower()
        if not re.match(r"^(how to|how do i|how can i|what command|which command)\b", normalized):
            return False

        if not tools:
            return True

        tool_names = {tool.get("function", {}).get("name") for tool in tools if tool.get("type") == "function"}
        tool_names.discard(None)
        return bool(tool_names and tool_names != {"lookup_tldr_command"})

    def _get_latest_user_message(self, messages: List[Dict[str, Any]]) -> str:
        """Extract the latest user text message from conversation history."""
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content
        return ""

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
        """Recursively remove unsupported fields and fix types for Google AI."""
        if not isinstance(schema, dict):
            return schema

        cleaned = {}
        for key, value in schema.items():
            # Handle the 'type' field specifically for Google API
            if key == "type":
                if isinstance(value, list):
                    # Google Gemini doesn't support list-based types (like nullable types)
                    # Pick the first non-null type and map it
                    types_list = [t for t in value if t != "null"]
                    if types_list:
                        cleaned[key] = types_list[0].upper()
                    else:
                        cleaned[key] = "NULL"
                elif isinstance(value, str):
                    # Ensure standard types are uppercase as expected by GenAI SDK
                    cleaned[key] = value.upper()
                else:
                    cleaned[key] = value
                continue

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

    def _format_messages_for_provider(self, messages: List[Dict[str, Any]]) -> List[types.Content]:
        """Convert OpenAI format messages to Google AI format."""
        contents = []
        tool_id_to_name = {}

        for message in messages:
            role = message.get("role")
            content = message.get("content")
            tool_calls = message.get("tool_calls")
            tool_call_id = message.get("tool_call_id")

            if role == "system":
                # Map system messages to user messages prefixed with "System:"
                parts = [types.Part(text=f"System: {content}")]
                contents.append(types.Content(role="user", parts=parts))

            elif role == "user":
                parts = [types.Part(text=content)]
                contents.append(types.Content(role="user", parts=parts))

            elif role == "assistant":
                parts = []
                if content:
                    parts.append(types.Part(text=content))

                if tool_calls:
                    if self.is_gemma_model:
                        # For Gemma, we must format tool calls as text, because it doesn't support native FunctionCall parts
                        tool_uses = []
                        for tc in tool_calls:
                            function = tc.get("function", {})
                            name = function.get("name")
                            args_str = function.get("arguments", "{}")
                            try:
                                args = json.loads(args_str) if isinstance(args_str, str) else args_str
                            except json.JSONDecodeError:
                                args = {}

                            # Cache ID for result lookup
                            tool_id = tc.get("id")
                            if tool_id:
                                tool_id_to_name[tool_id] = name

                            tool_uses.append({"tool_name": name, "arguments": args})

                        json_block = "```json\n" + json.dumps({"tool_uses": tool_uses}, indent=2) + "\n```"
                        parts.append(types.Part(text=json_block))
                    else:
                        # Native Gemini tool calls
                        for tc in tool_calls:
                            function = tc.get("function", {})
                            name = function.get("name")
                            args_str = function.get("arguments", "{}")
                            try:
                                args = json.loads(args_str) if isinstance(args_str, str) else args_str
                            except json.JSONDecodeError:
                                args = {}

                            tool_id = tc.get("id")
                            if tool_id:
                                tool_id_to_name[tool_id] = name

                            function_call = types.FunctionCall(
                                id=tool_id,
                                name=name,
                                args=args,
                            )
                            parts.append(
                                types.Part(
                                    function_call=function_call,
                                    thought=tc.get("thought"),
                                    thought_signature=tc.get("thought_signature"),
                                )
                            )

                if parts:
                    contents.append(types.Content(role="model", parts=parts))

            elif role == "tool":
                # Look up the function name using the tool_call_id
                name = tool_id_to_name.get(tool_call_id)

                if self.is_gemma_model:
                    # For Gemma, format tool output as text
                    result_text = f"Tool Result ({name or 'unknown'}):\n{content}"
                    parts = [types.Part(text=result_text)]
                    contents.append(types.Content(role="user", parts=parts))
                else:
                    if not name:
                        # Fallback if we can't find the name
                        parts = [types.Part(text=f"Tool Result (unknown function): {content}")]
                        contents.append(types.Content(role="user", parts=parts))
                    else:
                        # Return proper FunctionResponse
                        response_data = {"content": content}
                        parts = [
                            types.Part(function_response=types.FunctionResponse(name=name, response=response_data))
                        ]
                        contents.append(types.Content(role="user", parts=parts))

        # Merge adjacent messages of the same role if necessary
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

    @property
    def is_gemma_model(self) -> bool:
        """Check if the current model is a Gemma model."""
        return "gemma" in self.model_name.lower()

    def _format_tools_for_system_prompt(self, tools: List[Dict[str, Any]]) -> str:
        """Format tools for inclusion in system prompt (for Gemma)."""
        prompt = "\n\nAVAILABLE TOOLS:\n"
        for tool in tools:
            if tool.get("type") == "function":
                func = tool.get("function", {})
                name = func.get("name")
                desc = func.get("description")
                params = json.dumps(func.get("parameters"), indent=2)
                prompt += f"- Name: {name}\n  Description: {desc}\n  Parameters: {params}\n\n"

        prompt += "TOOL USAGE INSTRUCTIONS:\n"
        prompt += "To use a tool, you MUST respond with a JSON object wrapped in markdown code blocks like this:\n"
        prompt += "```json\n"
        prompt += (
            '{\n  "tool_uses": [\n    {\n      "tool_name": "example_tool",\n      "arguments": {\n        "param1":'
            ' "value1"\n      }\n    }\n  ]\n}\n'
        )
        prompt += "```\n"
        prompt += "If you are not using a tool, do not output this JSON format."
        return prompt

    def _parse_tool_calls_from_text(self, text: str) -> List[ToolCall]:
        """Parse tool calls from text response."""
        tool_calls = []
        try:
            # Look for JSON blocks
            json_pattern = r"```json\s*(\{.*?\})\s*```"
            matches = re.findall(json_pattern, text, re.DOTALL)

            for match in matches:
                try:
                    data = json.loads(match)
                    uses = data.get("tool_uses", [])
                    for use in uses:
                        name = use.get("tool_name")
                        args = use.get("arguments", {})
                        if name:
                            tool_calls.append(
                                ToolCall(name=name, arguments=args, call_id=f"call_{name}_{hash(str(args))}")
                            )
                except json.JSONDecodeError:
                    continue

        except Exception:
            pass

        return tool_calls

    def set_model(self, model: str):
        """Change the model being used."""
        self.model_name = model
        self.config.google_model = model

    def get_system_prompt(self) -> str:
        """Get the system prompt for the LLM."""
        return f"""You are Hash, a command-focused terminal assistant.

Key capabilities:
- Execute shell commands (with user permission)
- Explain shell commands and terminal workflows
- Use integrated tldr lookups to ground command syntax and examples when needed

Guidelines:
- Be concise and keep responses under {self.config.max_response_tokens} tokens unless the user explicitly requests more
- Never ask for execution confirmation in plain text. The CLI handles confirmation, including destructive shell commands.
- Provide command explanations when helpful
- Suggest alternatives when appropriate
- Prioritize security and best practices
- Indicate when you're unsure and suggest verification steps
- **Prefer simple, single-line commands** (e.g., `seq`, `grep`, `find`) over complex shell loops or scripts. Specifically, use `seq` for number sequences.
- Shell operators `|` and `;` are {'allowed' if self.config.allow_shell_operators else 'disabled'}; only use them when allowed.
- Stay within command assistance. Do not position yourself as a general debugging, code-analysis, or workflow-automation agent.

Tool usage policy:
- **Action Requests:** If the user asks you to perform a shell action or retrieve command output directly (e.g., "show me disk usage", "list files", "check time"), **CALL THE TOOL DIRECTLY**. Do not ask for confirmation in text; the CLI handles that.
- **Command-Hint Requests:** If the user explicitly provides a command hint (for example: "Use `find` as command hint"), treat it as an execution request and **CALL THE TOOL DIRECTLY** using that hint.
- **Command Lookup:** If the user asks about a specific command and you need grounded syntax, examples, or option details, call `lookup_tldr_command` before answering. Prefer this for uncommon, platform-specific, or low-confidence command questions.
- **Informational/How-to Requests:** If the user asks *how* to do something that involves a command (e.g., "how do I check disk usage", "explain ls command"), provide a text explanation. Use `lookup_tldr_command` if you need grounded command details, but do not execute shell commands for explanation-only requests. On the last line of your response, output exactly: `SUGGESTED_COMMAND: <command>` (where `<command>` is the full command string to execute).
- **General Knowledge:** For questions unrelated to command-line usage, answer briefly and redirect to the closest command-focused interpretation when possible. Do not use `echo` commands for plain text answers. Exception: If the answer depends on the current date/time (e.g. "how old is X"), you MUST use `execute_shell_command` with `date`.
- **Time/Date:** For ANY query involving "today", "now", or calculating relative dates/ages, you MUST use `execute_shell_command` with `date` to obtain the system date.
- **System Checks:** For local checks available via shell (OS, username, directory), use the shell tool when execution is appropriate.
- **Ambiguity:** If unsure whether to execute, err on the side of explaining (text response).

You have access to tools that can interact with the system. Use them appropriately to assist the user effectively."""
