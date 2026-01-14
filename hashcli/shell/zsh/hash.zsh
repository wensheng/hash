#!/usr/bin/env zsh
# Hash CLI Integration for zsh
# This script enables the # prefix trigger for Hash commands

# Create hash-magic-execute widget
hash-magic-execute() {
    # Check if buffer starts with ##
    if [[ "$BUFFER" =~ ^[[:space:]]*## ]]; then
        # Treat as comment / normal shell behavior
        zle .accept-line
    # Check if buffer starts with #
    elif [[ "$BUFFER" =~ ^[[:space:]]*# ]]; then
        # Save the original buffer for history
        local original_buffer="$BUFFER"

        # Extract command after #
        local cmd="${BUFFER#*#}"
        cmd="${cmd#"${cmd%%[![:space:]]*}"}"  # Trim leading whitespace

        # Execute hashcli with the command
        if [[ -n "$cmd" ]]; then
            echo  # New line for output
            hashcli "$cmd"
            local exit_code=$?
            echo  # Another new line

            # Show exit status if non-zero
            if [[ $exit_code -ne 0 ]]; then
                echo "Exit code: $exit_code"
            fi
        else
            # Empty command after #, just show help
            echo  # New line
            hashcli "/help"
            echo  # Another new line
        fi

        # Add to shell history
        print -s "$original_buffer"

        # Clear buffer and reset
        BUFFER=""
        zle reset-prompt
    else
        # Normal command execution
        zle .accept-line
    fi
}

# Create and bind the widget
zle -N hash-magic-execute
bindkey '^M' hash-magic-execute  # Bind to Enter key

# Function to check if hashcli is available
hash-check-availability() {
    if ! command -v hashcli >/dev/null 2>&1; then
        echo "Warning: hashcli command not found. Please ensure Hash CLI is installed and in your PATH."
        return 1
    fi
    return 0
}

# Initialize completion system if not already done
if [[ -z "$_comps" ]]; then
    autoload -Uz compinit
    compinit
fi

# Load completions for hash if available
if [[ -f "${0:h}/_hash" ]]; then
    fpath=("${0:h}" $fpath)
    autoload -Uz _hash
fi

# Optional: Add a function to toggle hash integration
hash-toggle() {
    if [[ "$ZLE_LINE_INIT" == *"hash-magic-execute"* ]]; then
        # Disable hash integration
        bindkey '^M' .accept-line
        echo "Hash integration disabled"
    else
        # Enable hash integration
        bindkey '^M' hash-magic-execute
        echo "Hash integration enabled"
    fi
}

# Check availability on load (non-blocking)
hash-check-availability >/dev/null 2>&1 || true

# Export functions for use in other contexts
export -f hash-toggle 2>/dev/null || true
