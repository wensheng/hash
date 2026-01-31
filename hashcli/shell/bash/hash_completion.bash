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

    # Extract the command name (after /)
    local proxy_cmd
    proxy_cmd="${proxy_content#*/}"
    proxy_cmd="${proxy_cmd%% *}"

    case "$proxy_cmd" in
        ls)
            # File and directory completions for ls command
            local opts="-l -a -h --long --all --human-readable"
            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
            else
                COMPREPLY=( $(compgen -f -- "$cur") )
            fi
            ;;
        model)
            # Available LLM models
            local models="gpt-4 gpt-4-turbo gpt-3.5-turbo claude-3-opus claude-3-sonnet claude-3-haiku gemini-pro gemini-1.5-pro"
            COMPREPLY=( $(compgen -W "$models" -- "$cur") )
            ;;
        fix)
            # Code fixing options and file completions
            if [[ "$cur" == *.* ]] || [[ -f "$cur" ]]; then
                # Complete file names
                COMPREPLY=( $(compgen -f -X '!*.@(py|js|ts|go|rs|java|cpp|c|rb|php)' -- "$cur") )
            else
                local fix_options="bug error performance security style logic syntax"
                COMPREPLY=( $(compgen -W "$fix_options" -- "$cur") )
            fi
            ;;
        config)
            # Configuration management options
            local config_options="show set get list reset"
            COMPREPLY=( $(compgen -W "$config_options" -- "$cur") )
            ;;
        clean)
            # No additional completions for clean
            COMPREPLY=()
            ;;
        help)
            # Help topics
            local help_topics="commands config models tools"
            COMPREPLY=( $(compgen -W "$help_topics" -- "$cur") )
            ;;
        "")
            # List available slash commands
            local slash_commands="clean model fix help config tldr"
            COMPREPLY=( $(compgen -W "$slash_commands" -P "/" -- "${cur#/}") )
            ;;
        *)
            # Try system command completion
            COMPREPLY=( $(compgen -c -- "$cur") )
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
        "explain this error"
        "help me with"
        "troubleshoot"
        "optimize"
        "find files"
        "fix permission issues"
        "debug this code"
        "review my code"
        "convert this"
        "automate this task"
        "secure my system"
        "backup my data"
        "monitor system performance"
        "configure"
        "install"
        "update"
        "network connectivity issues"
        "git commands help"
        "docker container"
        "kubernetes"
        "database query"
        "API integration"
        "testing strategy"
        "deployment"
    )

    # Generate completions from suggestions
    COMPREPLY=( $(compgen -W "${llm_suggestions[*]}" -- "$cur") )
}

# Register completion for hashcli command
complete -F _hash_completion hashcli

# Also register for hash alias if it exists
complete -F _hash_completion hash 2>/dev/null || true
