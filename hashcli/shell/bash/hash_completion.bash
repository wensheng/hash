#!/usr/bin/env bash
# Hash CLI completion system for bash
# Provides intelligent completions for both LLM and command proxy modes

_hash_completion() {
    local cur prev words cword
    _init_completion || return

    # Get the full line and check if it starts with #
    local line="${COMP_LINE}"
    local trimmed_line="${line#"${line%%[![:space:]]*}"}"

    # If line starts with #, provide hash-specific completions
    if [[ "$trimmed_line" =~ ^# ]]; then
        # Extract content after #
        local hash_content="${trimmed_line#*#}"
        hash_content="${hash_content#"${hash_content%%[![:space:]]*}"}"

        # Mode detection: check if starts with /
        if [[ "$hash_content" =~ ^/ ]]; then
            _hash_command_proxy_mode "$hash_content"
        else
            _hash_llm_mode "$hash_content"
        fi
    fi
}

# Command proxy mode completions
_hash_command_proxy_mode() {
    local proxy_content="$1"
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local slash_commands="help history"

    # Extract the command name (after /)
    local proxy_cmd
    proxy_cmd="${proxy_content#*/}"
    proxy_cmd="${proxy_cmd%% *}"

    case "$proxy_cmd" in
        help)
            # Help topics
            local help_topics="$slash_commands commands"
            COMPREPLY=( $(compgen -W "$help_topics" -- "$cur") )
            ;;
        history)
            # History options
            local history_options="list show clear"
            COMPREPLY=( $(compgen -W "$history_options" -- "$cur") )
            ;;
        "")
            # List available slash commands
            COMPREPLY=( $(compgen -W "$slash_commands" -P "/" -- "${cur#/}") )
            ;;
        *)
            # Unknown slash command: suggest built-in slash commands
            COMPREPLY=( $(compgen -W "$slash_commands" -P "/" -- "${cur#/}") )
            ;;
    esac
}

# LLM mode completions
_hash_llm_mode() {
    local llm_content="$1"
    local cur="${COMP_WORDS[COMP_CWORD]}"

    # Provide contextual suggestions based on partial input
    local -a llm_suggestions=(
        "how do I"
        "what command"
        "explain"
        "show me examples for"
        "find files"
        "check disk usage"
        "convert this"
        "backup my data"
        "configure"
        "install"
        "update"
        "git commands help"
        "docker command"
    )

    # Generate completions from suggestions
    COMPREPLY=( $(compgen -W "${llm_suggestions[*]}" -- "$cur") )
}

# Register completion for hashcli command
complete -F _hash_completion hashcli

# Also register for hash alias if it exists
complete -F _hash_completion hash 2>/dev/null || true
