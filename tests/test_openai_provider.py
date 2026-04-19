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


def test_openai_system_prompt_delegates_confirmation_to_cli():
    """Provider prompt should not tell the model to ask for destructive confirmation in text."""
    config = HashConfig(
        llm_provider=LLMProvider.OPENAI,
        openai_api_key="test-openai-key",
        openai_model="gpt-5-nano",
    )
    provider = OpenAIProvider(config)

    prompt = provider.get_system_prompt()

    assert "Never ask for execution confirmation in plain text." in prompt
    assert "Always ask for confirmation before executing potentially destructive commands" not in prompt
