#!/usr/bin/env zsh
# Hash CLI Integration for zsh
# This script enables interception for command lines starting with a single #

if [[ -z "$HASHCLI_SESSION_ID" ]]; then
    export HASHCLI_SESSION_ID=$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())' 2>/dev/null || echo "$$-$(date +%s)")
fi

# Create hash-magic-execute widget
hash-magic-execute() {
    local trimmed_buffer="${BUFFER#"${BUFFER%%[![:space:]]*}"}"

    # Keep ## as a plain shell comment escape.
    if [[ "$trimmed_buffer" == "##"* ]]; then
        BUFFER=""
        zle reset-prompt
    # Intercept command lines starting with a single '#'.
    elif [[ "$trimmed_buffer" == "#"* ]]; then
        # Save the original buffer for history
        local original_buffer="$BUFFER"

        # Execute hashcli with the full original command line
        zle -I
        hashcli "$original_buffer" < /dev/tty
        local exit_code=$?
        echo
        echo  # in zsh last line got cut off, not sure why.

        # Show exit status if non-zero
        if [[ $exit_code -ne 0 ]]; then
            echo "Exit code: $exit_code"
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
