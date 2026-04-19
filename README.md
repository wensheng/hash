<div align="center">

# Hash CLI

### Your AI Command Assistant for the Terminal

Talk to your terminal in plain English. Get instant help with commands, examples, and safe execution suggestions without leaving your shell.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://badge.fury.io/py/hashcli.svg)](https://badge.fury.io/py/hashcli)

[Quick Start](#quick-start) • [Features](#features) • [Examples](#real-world-examples) • [Plugins](#extensible-plugin-system) • [Docs](#configuration)

</div>

---

## What is Hash CLI?

Hash CLI turns your terminal into a command assistant. Instead of memorizing commands, googling syntax, or switching between tabs, just ask your terminal what to do in natural language. Hash CLI explains commands, suggests the right invocation, and executes safely when you want it to.

**Stop doing this:**
```bash
# Opens browser, searches "how to find large files linux"
# Copies command, pastes back
# Searches again for a different flag combination...
```

**Start doing this:**
```bash
# find all files larger than 10MB in current directory
```

Hash CLI figures out the command, shows you what it will run, and executes it with your approval.

---

## Why Choose Hash CLI?

### **Work Faster**
No more context switching. Ask command questions, get examples, and execute commands without leaving your terminal. Hash CLI integrates seamlessly into your existing workflow with optional `#` prefix support.

### **Stay Safe**
Built-in guardrails protect your system. Every destructive operation requires confirmation. Commands are checked against allow/deny lists. Dangerous patterns are blocked automatically.

### **Choose Your Brain**
Use OpenAI, Anthropic (Claude), or Google (Gemini) models. Switch providers and models on the fly. Your choice, your control.

### **Extend Infinitely**
The plugin system lets you add custom slash commands in minutes. Install community plugins or write your own. Make Hash CLI work exactly how you want.

### **Keep Context**
Full conversation history is preserved. Reference past sessions, review what worked, and build on previous interactions.

---

## Quick Start

### Installation

Install Hash CLI with pipx (recommended) or uv:

```bash
# Using pipx (works across all virtual environments)
pipx install hashcli

# Or using uv
uv tool install hashcli

# Or using pip
pip install hashcli
```

### Setup

Run the guided setup to configure your provider and install shell integration for the magical `#` prefix:

```bash
hashcli --config
```

### Start Using

That's it! Now start asking:

```bash
# show disk usage in human readable format
# what git branches exist and which one am I on
# find all python files modified in the last week
# explain xargs
```

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

- **OpenAI** (GPT-4, GPT-3.5)
- **Anthropic** (Claude Opus, Sonnet, Haiku)
- **Google** (Gemini Pro, Gemini Flash)

Switch models and providers instantly with CLI flags or config files.

### Intelligent Tool Calling

Hash CLI can:
- **Execute shell commands** with timeout protection and security checks
- **Explain commands** and suggest safe invocations
- **Ground command answers** with integrated `tldr` examples when needed

Every tool action is transparent and requires your approval.

### Extensible Plugin System

Create custom slash commands for your specific workflow:

```bash
# Install a plugin
hashcli --add-cmd plugins/model.py

# Use it
hashcli /model list
hashcli /model switch gpt-4
```

Build plugins in minutes. Check `plugins/` directory for examples.

### Smart Configuration

Hash CLI adapts to your environment:

1. CLI flags (highest priority)
2. Environment variables (`HASHCLI_*`)
3. User config (`~/.hashcli/config.toml`)
4. System config (`/etc/hashcli/config.toml`)
5. Sensible defaults

Override anything, anytime, from anywhere.

### Conversation History

Every interaction is saved:

```bash
hashcli /history          # List all sessions
hashcli /history show 5   # View session details
hashcli /history clear    # Clean up old conversations
```

Within an interactive shell, `hashcli` and `hcli` now reuse one conversation per shell session.
Use `--new-session` to start a fresh conversation for a single invocation.
If `HASHCLI_SESSION_ID` is set, that value is used instead of auto shell-session scoping.

Review past solutions, replay successful commands, and learn from history.

---

## Safety & Security

Hash CLI is designed with security as a first-class feature:

**Confirmation Required**
- Shell execution requires explicit approval
- File writes show exactly what will change
- Destructive operations are highlighted

**Command Filtering**
- Blocked commands list prevents dangerous operations
- Optional allowed commands whitelist for strict environments
- Shell operators (`|`, `;`, `&&`) are restricted by default

**Transparent Execution**
- Every command is shown before execution
- Tool calls are logged and auditable
- No hidden actions or background processes

**Configurable Paranoia**
- Configure confirmation behavior in `~/.hashcli/config.toml`
- Customize allow/deny lists per environment
- Set timeouts to prevent runaway processes

---

## Advanced Configuration

### Provider & Model Selection

```bash
# Use specific model
hashcli --model gpt-4 "explain quantum computing"

# Switch provider
hashcli --provider anthropic "debug my code"

# Show current config
hashcli --show-config
```

### Quiet & Streaming Modes

```bash
# Minimal output for scripting
hashcli --quiet "list all .py files"

# Stream responses as they generate
hashcli --stream "write a long explanation"
```

### Environment Variables

```bash
# Set default provider
export HASHCLI_PROVIDER=anthropic
export HASHCLI_MODEL=claude-opus-4

# Configure behavior
export HASHCLI_NO_CONFIRM=true
export HASHCLI_DEBUG=true
```

### Configuration File

Create `~/.hashcli/config.toml`:

```toml
[default]
provider = "anthropic"
model = "claude-sonnet-4"
stream = true
require_confirmation = true

[providers.anthropic]
api_key = "your-key-here"

[security]
blocked_commands = ["rm -rf /", "dd if=", "mkfs"]
allowed_commands = []  # empty = allow all (except blocked)
```

---

## Plugin Development

### Create Your Own Plugin

```python
# my_plugin.py
from typing import List
from hashcli.command_proxy import Command
from hashcli.config import HashConfig

class MyPluginCommand(Command):
    """Custom functionality for my workflow"""

    def execute(self, args: List[str], config: HashConfig) -> str:
        # Your plugin logic here
        return f"Plugin executed with args: {args}"

    def get_help(self) -> str:
        return "Usage: /my-plugin [args]\nDoes something useful."

    def validate_args(self, args: List[str]) -> tuple[bool, str]:
        return True, ""
```

### Install & Use

```bash
# Install your plugin
hashcli --add-cmd my_plugin.py

# Use it immediately
hashcli /my-plugin arg1 arg2
```

Plugins are stored in `~/.hashcli/plugins/` and loaded automatically on startup.

---

## CLI Reference

```bash
# Interactive mode
hashcli "your question here"

# Configuration
hashcli --config          # Guided setup wizard + shell integration
hashcli --show-config     # Display current settings

# Plugin management
hashcli --add-cmd <file>  # Install plugin

# Built-in commands
hashcli /help             # Show available commands
hashcli /history          # Manage conversation history
hashcli /model list       # List available models (with plugin)

# Flags
--model MODEL             # Override model
--provider PROVIDER       # Override provider (openai/anthropic/google)
--new-session             # Start a fresh conversation for this run
--debug, -d               # Enable debug output
--quiet, -q               # Minimal output
--stream                  # Stream responses
```

---

## Contributing

Hash CLI is open source and welcomes contributions:

- Report bugs and request features via [GitHub Issues](https://github.com/wensheng/hashcli0/issues)
- Submit pull requests for bug fixes and enhancements
- Share your plugins with the community
- Improve documentation and examples

---

## License

Hash CLI is released under the MIT License. See LICENSE file for details.

---

## Support

- **Documentation**: Check this README and `CLAUDE.md` for development details
- **Issues**: [GitHub Issues](https://github.com/wensheng/hashcli0/issues)
- **Questions**: Open a GitHub Discussion or Issue

---

<div align="center">

**Stop memorizing commands. Start talking to your terminal.**

[Install Now](#installation) • [Read the Docs](#configuration) • [Write a Plugin](#plugin-development)

</div>
