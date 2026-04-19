from hashcli.config import HashConfig, LLMProvider
from hashcli.providers.openai_provider import OpenAIProvider


def test_format_messages_for_responses_does_not_emit_invalid_assistant_ids():
    """Assistant history messages for Responses API should not include synthetic invalid IDs."""
    config = HashConfig(
        llm_provider=LLMProvider.OPENAI,
        openai_api_key="test-openai-key",
        openai_model="gpt-5-nano",
    )
    provider = OpenAIProvider(config)

    messages = [
        {"role": "user", "content": "Explain tar"},
        {"role": "assistant", "content": "tar archives files."},
    ]

    formatted = provider._format_messages_for_responses(messages)

    assert formatted[0]["role"] == "user"
    assert formatted[1]["role"] == "assistant"
    assert formatted[1]["type"] == "message"
    assert "id" not in formatted[1]
    assert "status" not in formatted[1]
