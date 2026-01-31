# Hashcli

## Overview

Hash (HAcker SHell) is an intelligent command-line interface (CLI) system that serves as a versatile terminal assistant. It operates as both an LLM-powered conversational agent and a command proxy system, offering two distinct interaction modes: natural language queries for assistance and slash-prefixed commands for direct functionality.

## Installation

```bash
pip install hashcli
```

**Shell Integration**

## Features

- **Dual-Mode Functionality**:
  - LLM Chat Mode: Natural language queries for intelligent assistance
  - Command Proxy Mode: Slash-prefixed commands for direct functionality
- **Multi-Provider LLM Support**: Works with OpenAI, Anthropic, and Google AI
- **Tool Calling**: Execute shell commands, read files, list directories
- **Conversation History**: Persistent storage of conversation sessions
- **Shell Integration**: Seamless integration with zsh and fish shells using `#` prefix
- **Cross-Platform**: Works on Linux, macOS, and Windows

## Usage

### LLM Chat Mode
```bash
# how do I list large files?
# explain this error: permission denied
# help me debug this python script
```

replace `#` with `hashcli`  if you do no want to set up shell integration.

### Command Proxy Mode
```bash
#/ls -la
#/model gpt-4
#/clean
#/fix "implement authentication"
```

Slash commands check for system commands first (e.g., `/ls`, `/grep`, `/find`) and execute them directly when available.

## Shell Integration

Hash can be integrated with your shell for a seamless experience using the `#` prefix:

```bash
# how do I check disk usage?
# /ls -la
```

## Docker Development Environment

This project includes a Docker environment for testing shell integrations:

1. Build the container:
```bash
docker-compose build
```

2. Start the container:
```bash
docker-compose up -d
```

3. Enter the container with zsh:
```bash
docker-compose exec hashcli-test zsh
```

4. Or enter with fish:
```bash
docker-compose exec hashcli-test fish
```

5. Test the shell integration:
```bash
# how do I list files?
# /ls -la
```

5. Run tests:
```bash
docker-compose exec hashcli-test ./test_installation.sh
```
