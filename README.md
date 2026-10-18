<div align="center">

# Hash

### Your terminal, but with a brain. (Works with your current zsh, bash, fish, or PowerShell.)

Talk to your terminal in plain English. Get instant help with commands, examples, and safe execution suggestions without leaving your shell.  You never have to context-switch to a browser again.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Built with Rust](https://img.shields.io/badge/built%20with-Rust-orange.svg)](https://www.rust-lang.org/)
[![PyPI version](https://badge.fury.io/py/hashcli.svg)](https://badge.fury.io/py/hashcli)

[Quick Start](#quick-start) • [Why](#why-hash-cli) • [Features](#features) • [Examples](#real-world-examples) • [Docs](#configuration)

</div>

---

## The Workflow

**Stop doing this:**
1. Open or switch to browser
2. Search "how to kill process on port 8888"
3. Sift through StackOverflow answers or blog posts from 2014
4. Copy-paste a command you only half-understand
5. A few days later, do the same all over again

**Start doing this:**

`$ hi kill whatever is running on port 8888`
```bash
Suggested command
Description: Finds process IDs listening on port 8888 and terminates them.
Command: lsof -ti :8888 | xargs kill -9
Choose [y] execute, [n] cancel, [x] explain, [e] edit [y/n/x/e] (n): x

# Hash explains each part, then asks again before running.
Choose [y] execute, [n] cancel, [x] explain, [e] edit [y/n/x/e] (n): y
# process running on 8888 is killed
```

---

## Quick Start

### Installation

Hash CLI is a single native binary. Install it with `uv` (recommended) or pip/pipx, which download a prebuilt wheel containing the `hashcli` and `hi` executables:

    uv tool install hashcli

```bash
pipx install hashcli
#or
pip install hashcli
```

Or build from source with Rust (1.88+):

```bash
cargo install --path .   # run inside this directory
```

### Setup

Run the guided setup to configure your provider, choose a model, optionally store an API key or use an environment variable, enable streaming, validate the provider, and install shell integration for the `#` prefix in supported shells:

```bash
hi --config
```

Shell integration setup supports zsh, bash, fish, and PowerShell.
If you run an interactive query before configuring Hash CLI and no provider API key is present in your environment, Hash CLI starts this setup flow automatically.

### Start Using Hash

After shell integration, you can use `#`. You can always use `hi` or `hashcli`.

```bash
hi show disk usage in human readable format
# what git branches exist and which one am I on
# find all python files modified in the last week
hi explain xargs
```

---

## Why Hash CLI?

### ⚡ Work Faster
Complex pipes, regex, and obscure flags are hard to memorize. Describe what you want—from `"how to rebase my last 3 commits into one"` to `"find and delete all .log files larger than 50MB"`—and get the exact syntax instantly.  Hash CLI integrates seamlessly into your existing workflow with optional `#` prefix support.

### ♾️ Stay in the Flow
Every time you leave the terminal to Google a command, you lose focus. Hash CLI keeps your hands on the home row and your project in sight. It’s not just a tool; it’s a productivity multiplier.  Ask command questions, get examples, and execute commands without leaving your terminal.

### 🛡️ Stay Safe
Built-in guardrails protect your system. Commands are checked against allow/deny lists, dangerous patterns are blocked automatically, and confirmation behavior can be enforced by config and query type.
1. **Explain:** It breaks down what the command actually does.
2. **Review:** You see the command before a single character is executed.
3. **Control:** Choose to run it as-is, edit it for your specific needs, or cancel.

### 🧠 Choose Your Brain
Use OpenAI or compatible, Anthropic (Claude), or Google (Gemini) models. Switch providers and models on the fly. Your choice, your control.

### 🧩 Extend Infinitely
The plugin system lets you add custom slash commands in minutes. Install community plugins or write your own. Make Hash CLI work exactly how you want.

### Key Capabilities:
* Natural Language Translation: From "find large files" to "undo my last git commit," it speaks your language.
* Contextual Explanations: Understand why a command works before you run it.
* Safety Interlocks: Destructive commands and how-to execution suggestions are staged for review, with config controls for stricter confirmation.
* No Tab-Hopping: Keep your hands on the home row and your focus on the code.
* Full conversation history is preserved. Reference past sessions, review what worked, and build on previous interactions.

---

## Real-World Examples

### Command Discovery

```bash
# explain tar
# what command finds files larger than 100MB
# show me examples for rsync
```

Hash CLI explains the command, grounds the answer with concise examples, and suggests the right invocation.

### System Administration

```bash
# show me which processes are using the most memory
# clean up docker images I'm not using
# backup my Documents folder to external drive
```

Perfect for sysadmins who want intelligent assistance without memorizing every flag.

### Development Workflow

```bash
# show last 10 commits with author names
# what command shows my current git branch
# find all python files modified in the last week
```

Streamline your daily development tasks with natural language.

### Learning & Discovery

```bash
# how do I use awk to extract the third column
# explain grep -E
# show me examples of find with -mtime
```

Your terminal becomes a patient teacher, explaining concepts and showing examples.

---

## Features

### Natural Language Interface

Ask command questions and give shell-oriented instructions in plain English. Hash CLI translates your intent into precise commands and actions.

### Multi-Provider AI Support

- **OpenAI** (including compatible OpenAI-style endpoints)
- **Anthropic** (Claude models)
- **Google** (Gemini models)

Switch models and providers instantly with CLI flags or config files.

### Intelligent Tool Calling

Hash CLI can:
- **Execute shell commands** with timeout protection and security checks
- **Explain commands** and suggest safe invocations
- **Ground command answers** with integrated `tldr` examples when needed

Tool calls are shown transparently, and confirmation behavior follows your config and query type.

### Extensible Plugin System

Create custom slash commands for your specific workflow:

```bash
# Install a plugin
hi --add-cmd plugins/hello.py

# Non-interactive install
hi --add-cmd plugins/hello.py --yes

# See installed plugins
hi --list-plugins

# Use it
hi /hello
hi /hello world
```

Install previews show the command name, destination path, and first help line before copying. Plugins are validated by running them with `--hashcli-help` before installation. Build plugins in minutes in any language; check `plugins/` for an example.

### Smart Configuration

Hash CLI adapts to your environment:

1. CLI flags (highest priority)
2. Environment variables (`HASHCLI_*`)
3. User config (`~/.hashcli/config.toml`)
4. System config (`/etc/hashcli/config.toml`)
5. Sensible defaults

Override anything, anytime, from anywhere.

Use command mode to inspect or edit config without opening the TOML file:

```bash
hi config get streaming
hi config set streaming true
hi config unset streaming
```

### Conversation History

Every interaction is saved:

```bash
hi /history                 # List recent sessions
hi /history show 42574d4e   # View session by full ID or unique prefix
hi /history search rsync    # Search saved messages
hi /history clear           # Clear all saved conversations
```

Within an interactive shell, `hashcli` and `hi` now reuse one conversation per shell session.
Use `--new-session` to start a fresh conversation for a single invocation.
Use `--session ID_OR_PREFIX` to resume a specific previous session.
If `HASHCLI_SESSION_ID` is set, that value is used instead of auto shell-session scoping.

Review past solutions, replay successful commands, and learn from history.

---

## Safety & Security

Hash CLI is designed with security as a first-class feature:

**Confirmation Behavior**
- `how to ...` queries force confirmation for tool calls and suggested command execution
- Other action-oriented queries follow `command_confirmation` for suggested command execution and `tool_confirmation` for tool calls
- Suggested command confirmation offers execute, cancel, explain, and edit choices
- Tool calls and command execution are shown before they run when confirmation is required

**Command Filtering**
- Blocked commands list prevents dangerous operations (entries match whole words, so `su` blocks `su -` but not `cat results.txt`)
- Optional allowed commands whitelist for strict environments
- Shell operators can be restricted via config

**Transparent Execution**
- Commands and tool arguments are shown before execution when confirmation is required
- Command output is returned directly to the terminal
- No hidden background processes

**Configurable Paranoia**
- Configure confirmation behavior in `~/.hashcli/config.toml`
- Customize allow/deny lists per environment
- Set timeouts to prevent runaway processes

---

## Advanced Configuration

### Provider & Model Selection

```bash
# Use specific model
hi --model gpt-5.2 "explain quantum computing"

# Switch provider
hi --provider anthropic "debug my code"

# Show current config
hi --show-config

# Persist config changes
hi config set llm_provider anthropic
hi config set anthropic_model claude-sonnet-4-6
```

### Quiet Mode

```bash
# Minimal output for scripting
hi --quiet "list all .py files"
```

Enable streaming with config or env vars, for example `HASHCLI_STREAMING=true hi "write a long explanation"`.

### Environment Variables

Environment variables map directly to top-level config keys with a `HASHCLI_` prefix. API keys also support the standard provider env vars.

```bash
# Set default provider
export HASHCLI_LLM_PROVIDER=anthropic
export HASHCLI_ANTHROPIC_MODEL=claude-sonnet-4-6

# API keys
export ANTHROPIC_API_KEY=your-key-here
# or:
export HASHCLI_ANTHROPIC_API_KEY=your-key-here

# Configure behavior
export HASHCLI_COMMAND_CONFIRMATION=true
export HASHCLI_TOOL_CONFIRMATION=true
export HASHCLI_SHOW_DEBUG=true
export HASHCLI_STREAMING=true
```

### Configuration File

Create `~/.hashcli/config.toml`:

```toml
llm_provider = "anthropic"
anthropic_model = "claude-sonnet-4-6"
anthropic_api_key = "your-key-here"
streaming = true
command_confirmation = true
tool_confirmation = true
show_debug = false
blocked_commands = ["rm -rf /", "dd if=", "mkfs"]
allowed_commands = []  # empty = allow all (except blocked)
```

---

## Plugin Development

### Create Your Own Plugin

A plugin is any executable file: a shell script, a Python script, a compiled binary, and so on. The command name is the lowercase file name without its extension (`deploy.sh` becomes `/deploy`).

The protocol:

- `<plugin> --hashcli-help` prints help text and exits 0. The first line is the summary shown in `/help`, `--list-plugins`, and shell completions.
- `<plugin> <args...>` runs the command. Whatever it prints to stdout is the result. stdin and stderr stay attached to your terminal, and a non-zero exit code is reported as an error.
- `HASHCLI_PLUGIN_NAME` is set to the command name, and `HASHCLI_PLUGIN_CONFIG` holds the effective configuration as JSON, with API keys removed.

```sh
#!/bin/sh
# deploy.sh
if [ "$1" = "--hashcli-help" ]; then
  echo "Deploy the current project"
  echo "  /deploy <env>   - Deploy to the given environment"
  exit 0
fi
echo "Deploying to ${1:-staging}..."
```

### Install & Use

```bash
# Install your plugin (copied to ~/.hashcli/plugins/ and made executable)
hi --add-cmd deploy.sh

# Use it immediately
hi /deploy production

# List or remove installed plugins
hi --list-plugins
hi --remove-cmd deploy
```

Plugins are stored in `~/.hashcli/plugins/` and discovered automatically. Built-in command names (`help`, `history`, `config`, `tldr`) are reserved.
Use `--yes` with `--add-cmd` or `--remove-cmd` for non-interactive scripts.

> Upgrading from the Python version: plugins that subclassed `hashcli.command_proxy.Command` no longer load. Rewrite them as standalone scripts using the protocol above (see `plugins/hello.py`).

---

## CLI Reference

```bash
# Interactive mode
hi "your question here"

# Configuration
hi --config          # Guided setup wizard + shell integration
hi --config-file F   # Use a specific config file
hi --show-config     # Display current settings
hi config get KEY    # Show an effective config value
hi config set KEY VALUE
hi config unset KEY

# Plugin management
hi --add-cmd <file>  # Preview, validate, and install plugin
hi --add-cmd <file> --yes
hi --list-plugins
hi --remove-cmd NAME

# Built-in commands
hi /help             # Show available commands
hi /history          # Manage conversation history
hi /history search QUERY
hi /tldr tar         # Quick examples for a command (tldr pages)

# Flags
--model MODEL             # Override model
--provider PROVIDER       # Override provider (openai/anthropic/google)
--new-session             # Start a fresh conversation for this run
--session ID_OR_PREFIX    # Resume a previous conversation
--debug, -d               # Enable debug output
--quiet, -q               # Minimal output
```

---

## Contributing

Hash CLI is open source and welcomes contributions:

- Report bugs and request features via [GitHub Issues](https://github.com/wensheng/hash/issues)
- Submit pull requests for bug fixes and enhancements
- Share your plugins with the community
- Improve documentation and examples

---

## License

Hash CLI is released under the MIT License. See LICENSE file for details.
