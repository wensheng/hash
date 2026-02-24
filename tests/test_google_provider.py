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

    messages = [
        {"role": "user", "content": "What time is it?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_123",
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
    assert formatted[1].parts[0].function_call.name == "execute_shell_command"
    assert formatted[1].parts[0].function_call.args["command"] == "date"

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
