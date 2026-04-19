import json
from types import SimpleNamespace

import pytest

from hashcli.providers.google_provider import GoogleProvider
from hashcli.config import HashConfig, LLMProvider


@pytest.fixture
def google_config():
    return HashConfig(
        llm_provider=LLMProvider.GOOGLE,
        google_api_key="test-google-key",
        google_model="gemini-pro",
    )


def test_format_messages_tool_cycle(google_config):
    """Test formatting of a conversation with tool calls and results."""
    provider = GoogleProvider(google_config)
    thought_signature = b"sig-123"

    messages = [
        {"role": "user", "content": "What time is it?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_123",
                "thought": True,
                "thought_signature": thought_signature,
                "type": "function",
                "function": {
                    "name": "execute_shell_command",
                    "arguments": '{"command": "date", "description": "check time"}',
                },
            }],
        },
        {"role": "tool", "content": "Sun Feb  1 12:00:00 UTC 2026", "tool_call_id": "call_123"},
    ]

    formatted = provider._format_messages_for_provider(messages)

    assert len(formatted) == 3

    # 1. User message
    assert formatted[0].role == "user"
    assert formatted[0].parts[0].text == "What time is it?"

    # 2. Assistant (Model) message with function call
    assert formatted[1].role == "model"
    # Note: Depending on my implementation, content might be empty so parts might only contain function_call
    assert len(formatted[1].parts) == 1
    assert formatted[1].parts[0].function_call is not None
    assert formatted[1].parts[0].function_call.id == "call_123"
    assert formatted[1].parts[0].function_call.name == "execute_shell_command"
    assert formatted[1].parts[0].function_call.args["command"] == "date"
    assert formatted[1].parts[0].thought is True
    assert formatted[1].parts[0].thought_signature == thought_signature

    # 3. Tool (User) message with function response
    assert formatted[2].role == "user"
    assert formatted[2].parts[0].function_response is not None
    assert formatted[2].parts[0].function_response.name == "execute_shell_command"
    assert formatted[2].parts[0].function_response.response["content"] == "Sun Feb  1 12:00:00 UTC 2026"


def test_format_messages_mixed_content(google_config):
    """Test formatting of assistant message with both text and tool calls."""
    provider = GoogleProvider(google_config)

    messages = [{
        "role": "assistant",
        "content": "I will check the time.",
        "tool_calls": [{
            "id": "call_456",
            "type": "function",
            "function": {
                "name": "read_file",
                "arguments": '{"file_path": "test.txt"}',
            },
        }],
    }]

    formatted = provider._format_messages_for_provider(messages)

    assert len(formatted) == 1
    assert formatted[0].role == "model"
    assert len(formatted[0].parts) == 2
    assert formatted[0].parts[0].text == "I will check the time."
    assert formatted[0].parts[1].function_call.name == "read_file"


@pytest.mark.asyncio
async def test_generate_response_disables_function_calling_for_how_to(google_config, mocker):
    """How-to prompts should disable Gemini function calling mode."""
    provider = GoogleProvider(google_config)
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="Use find", function_call=None)]))
        ],
        usage_metadata=None,
    )
    generate_content = mocker.AsyncMock(return_value=response)
    mocker.patch.object(provider.client.aio.models, "generate_content", generate_content)

    tools = [{
        "type": "function",
        "function": {
            "name": "execute_shell_command",
            "description": "Execute shell command",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}, "description": {"type": "string"}},
                "required": ["command", "description"],
            },
        },
    }]

    await provider.generate_response(
        messages=[{"role": "user", "content": "how to find all __pycache__ directories"}],
        tools=tools,
    )

    config = generate_content.call_args.kwargs["config"]
    assert config.tool_config is not None
    assert config.tool_config.function_calling_config.mode == "NONE"


@pytest.mark.asyncio
async def test_generate_response_keeps_function_calling_auto_for_action_request(google_config, mocker):
    """Action prompts should keep default function calling behavior."""
    provider = GoogleProvider(google_config)
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="Running", function_call=None)]))
        ],
        usage_metadata=None,
    )
    generate_content = mocker.AsyncMock(return_value=response)
    mocker.patch.object(provider.client.aio.models, "generate_content", generate_content)

    tools = [{
        "type": "function",
        "function": {
            "name": "execute_shell_command",
            "description": "Execute shell command",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}, "description": {"type": "string"}},
                "required": ["command", "description"],
            },
        },
    }]

    await provider.generate_response(
        messages=[{"role": "user", "content": "find all __pycache__ directories"}],
        tools=tools,
    )

    config = generate_content.call_args.kwargs["config"]
    assert config.tool_config is None


@pytest.mark.asyncio
async def test_generate_response_preserves_thought_signature_in_tool_metadata(google_config, mocker):
    """Native Gemini function calls should retain thought-signature metadata for follow-up turns."""
    provider = GoogleProvider(google_config)
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            text=None,
                            thought=True,
                            thought_signature=b"sig-456",
                            function_call=SimpleNamespace(
                                id="fc-1",
                                name="lookup_tldr_command",
                                args={"command": "tar", "platform": None, "language": None, "search": False},
                            ),
                        )
                    ]
                )
            )
        ],
        usage_metadata=None,
    )
    generate_content = mocker.AsyncMock(return_value=response)
    mocker.patch.object(provider.client.aio.models, "generate_content", generate_content)

    result = await provider.generate_response(
        messages=[{"role": "user", "content": "show me tar examples"}],
        tools=[],
    )

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].call_id == "fc-1"
    assert result.tool_calls[0].metadata["thought"] is True
    assert result.tool_calls[0].metadata["thought_signature"] == b"sig-456"


@pytest.mark.asyncio
async def test_generate_response_keeps_tldr_lookup_enabled_for_how_to(google_config, mocker):
    """How-to prompts may still use tool calling when tldr lookup is the only tool."""
    provider = GoogleProvider(google_config)
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="Use tar", function_call=None)]))
        ],
        usage_metadata=None,
    )
    generate_content = mocker.AsyncMock(return_value=response)
    mocker.patch.object(provider.client.aio.models, "generate_content", generate_content)

    tools = [{
        "type": "function",
        "function": {
            "name": "lookup_tldr_command",
            "description": "Lookup tldr examples",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }]

    await provider.generate_response(
        messages=[{"role": "user", "content": "how do I extract a tar.gz file"}],
        tools=tools,
    )

    config = generate_content.call_args.kwargs["config"]
    assert config.tool_config is None


def test_clean_parameter_schema_nullable_and_uppercase(google_config):
    """Test that _clean_parameter_schema handles nullable types and converts to uppercase."""
    provider = GoogleProvider(google_config)

    schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "platform": {"type": ["string", "null"]},
            "language": {"type": ["string", "null"]},
            "count": {"type": "integer"},
            "nested": {
                "type": "object",
                "properties": {
                    "tags": {"type": "array", "items": {"type": "string"}}
                }
            }
        },
        "required": ["command", "platform", "language", "count"],
        "additionalProperties": False,
    }

    cleaned = provider._clean_parameter_schema(schema)

    assert cleaned["type"] == "OBJECT"
    assert cleaned["properties"]["command"]["type"] == "STRING"
    assert cleaned["properties"]["platform"]["type"] == "STRING"
    assert cleaned["properties"]["language"]["type"] == "STRING"
    assert cleaned["properties"]["count"]["type"] == "INTEGER"
    assert cleaned["properties"]["nested"]["type"] == "OBJECT"
    assert cleaned["properties"]["nested"]["properties"]["tags"]["type"] == "ARRAY"
    assert cleaned["properties"]["nested"]["properties"]["tags"]["items"]["type"] == "STRING"
    assert "additionalProperties" not in cleaned


def test_google_system_prompt_delegates_confirmation_to_cli(google_config):
    """Provider prompt should direct Gemini to call tools instead of asking for text confirmation."""
    provider = GoogleProvider(google_config)

    prompt = provider.get_system_prompt()

    assert "Never ask for execution confirmation in plain text." in prompt
    assert "Always ask for confirmation before executing potentially destructive commands" not in prompt
