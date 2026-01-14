#!/usr/bin/env bash
# Hash CLI Integration for bash
# This script enables the # prefix trigger for Hash commands

# Function to handle hash magic execution
hash_magic_execute() {
    local line="$READLINE_LINE"
    local trimmed_line="${line#"${line%%[![:space:]]*}"}"  # Trim leading whitespace

    # Check if line starts with ##
    if [[ "$trimmed_line" =~ ^## ]]; then
        # Treat as comment / normal shell behavior
        return
    # Check if line starts with #
    elif [[ "$trimmed_line" =~ ^# ]]; then
        # Save the original line for history
        local original_line="$line"

        # Extract command after #
        local cmd="${trimmed_line#*#}"
        cmd="${cmd#"${cmd%%[![:space:]]*}"}"  # Trim leading whitespace

        # Clear the current line
        READLINE_LINE=""
        READLINE_POINT=0

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
        history -s "$original_line"
    else
        # Normal command execution - let readline handle it
        return
    fi
}

# Bind the function to the Enter key
# We use RETURN (Enter key) to trigger our custom handler
bind -x '"\C-m": hash_magic_execute'
bind -x '"\C-j": hash_magic_execute'  # Also bind Ctrl+J (alternative newline)

# Function to check if hashcli is available
hash_check_availability() {
    if ! command -v hashcli >/dev/null 2>&1; then
        echo "Warning: hashcli command not found. Please ensure Hash CLI is installed and in your PATH."
        return 1
    fi
    return 0
}

# Load bash completions if available
if [[ -f "${BASH_SOURCE[0]%/*}/hash_completion.bash" ]]; then
    source "${BASH_SOURCE[0]%/*}/hash_completion.bash"
fi

# Optional: Add a function to toggle hash integration
hash_toggle() {
    # Check current binding
    local current_binding
    current_binding=$(bind -p | grep '"\C-m"')

    if [[ "$current_binding" == *"hash_magic_execute"* ]]; then
        # Disable hash integration - restore default behavior
        bind '"\C-m": accept-line'
        bind '"\C-j": accept-line'
        echo "Hash integration disabled"
    else
        # Enable hash integration
        bind -x '"\C-m": hash_magic_execute'
        bind -x '"\C-j": hash_magic_execute'
        echo "Hash integration enabled"
    fi
}

# Check availability on load (non-blocking)
hash_check_availability >/dev/null 2>&1 || true

# Export functions for use in other contexts
export -f hash_toggle 2>/dev/null || true
export -f hash_check_availability 2>/dev/null || true
