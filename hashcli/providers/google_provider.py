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
            # Prepare messages and tools
            if self.is_gemma_model and tools:
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
                            
                            tool_uses.append({
                                "tool_name": name,
                                "arguments": args
                            })
                        
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

                            parts.append(types.Part(function_call=types.FunctionCall(name=name, args=args)))

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
                        parts = [types.Part(function_response=types.FunctionResponse(name=name, response=response_data))]
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
        prompt += '{\n  "tool_uses": [\n    {\n      "tool_name": "example_tool",\n      "arguments": {\n        "param1": "value1"\n      }\n    }\n  ]\n}\n'
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
                                ToolCall(
                                    name=name,
                                    arguments=args,
                                    call_id=f"call_{name}_{hash(str(args))}"
                                )
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
- **Informational/How-to Requests:** If the user asks *how* to do something that involves a command (e.g., "how do I check disk usage", "explain ls command"), provide a text explanation. **DO NOT call the tool**. Instead, append a final line exactly: "do you want execute `<command>`?" (where `<command>` is the **full command string with all arguments**, e.g., `ls -la`, wrapped in backticks).
- **General Knowledge:** For questions unrelated to system operations (e.g. "why is the sky blue"), simply answer the question. DO NOT append the "do you want execute" line. DO NOT use `echo` commands for plain text answers. Exception: If the answer depends on the current date/time (e.g. "how old is X"), you MUST use `execute_shell_command` with `date`.
- **Time/Date:** For ANY query involving "today", "now", or calculating relative dates/ages, you MUST use `execute_shell_command` with `date` to obtain the system date.
- **Web Search:** Use the `web_search` tool only when the user explicitly asks to search/browse or requests sources, or when the answer is time-sensitive/likely to change (e.g., current events, prices, schedules). Do **not** use it for general knowledge or explanatory questions (e.g., "why is the sky blue").
- **System Checks:** For local checks (OS, username, directory), use the appropriate tool.
- **Ambiguity:** If unsure whether to execute, err on the side of explaining (text response).

Never include the confirmation line ("do you want execute...") if you are calling a tool. That line is ONLY for text-based suggestions where you did NOT call a tool.

You have access to tools that can interact with the system. Use them appropriately to assist the user effectively."""
