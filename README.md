# Hash CLI

Hash (HAcker SHell) is an intelligent terminal assistant that combines LLM-powered conversational assistance with command-line productivity tools. Ask questions in plain English, manage your development workflow, and keep your shell flow fast without losing control or safety.

## Why Hash CLI

- **Conversational AI**: Natural language interface for coding help, debugging, and system administration.
- **Provider choice**: OpenAI, Anthropic, and Google models supported.
- **Tool calling with guardrails**: shell execution, file ops, web search, and code analysis with confirmations and policy checks.
- **Conversation history**: list, inspect, and clear past sessions.
- **Shell integration**: optional `#` prefix for a seamless inline workflow.
- **Rich output**: streaming, panels, and quiet mode when needed.

## Quickstart

Install Hashcli:
```bash
pipx install hashcli
```
or
```bash
uv tool install hashcli
```

You can also use `pip install hashcli`, but we recommend using `pipx` or `uv tool` as it makes hashcli available on all virtual environments.

Set up Shell integration:
```bash
hashcli --setup
```

Set up api key in your terminal:

    export OPENAI_API_KEY="your-key"

If you prefer a guided setup:
```bash
hashcli --config
```

## Usage

```bash
# show me the current disk usage in human readable format
# show last 5 git commit messages
# how to find all __pycache__ in current folder
```

## Features (from the current code)

- **Built-in Commands**: `/clean`, `/config`, `/fix`, `/help`, `/history`, `/model`, `/tldr`, `/exit`, `/quit`
- **LLM Tools**:
  - `execute_shell_command` (guarded shell execution with timeouts and allow/block lists)
  - `read_file`, `write_file`, `list_directory` (filesystem operations with safety checks)
  - `web_search` (DuckDuckGo via `ddgs`)
  - `analyze_code` (AST-based Python analysis and lightweight JS/Java metrics)
- **History management**: list sessions, show a session, clear old or all history
- **Streaming output**: optional streaming for responses
- **Interactive config wizard**: `hashcli --config` for provider, model, and API key setup

## Configuration

Hash CLI loads configuration in this order (highest to lowest):

1. CLI flags
2. Environment variables (`HASHCLI_` prefix)
3. User config (`~/.hashcli/config.toml`)
4. System config (`/etc/hashcli/config.toml` on Unix)
5. Defaults

Provider key fallbacks:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY` or `GEMINI_API_KEY`

Useful flags:

```bash
hashcli --model gpt-5-mini
hashcli --provider anthropic
hashcli --no-confirm
hashcli --quiet
hashcli --show-config
```

## Safety Notes

- Shell execution and file writes require confirmation by default.
- LLM tool calls are checked against allow/deny lists and dangerous patterns.
- Shell operators (`|` and `;`) are blocked unless explicitly enabled.
