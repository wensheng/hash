#!/bin/bash

# Script to set up shell integrations for testing

set -e

echo "Setting up shell integrations for hashcli..."

# Create necessary directories
mkdir -p ~/.config/fish
mkdir -p ~/.hashcli

# Set up zsh integration
echo "Setting up zsh integration..."
cat > ~/.zshrc << 'EOF'
# Enable zsh completion and widgets
autoload -Uz compinit && compinit
autoload -Uz bashcompinit && bashcompinit

# Hashcmd zsh integration
hash-magic-execute() {
    if [[ $BUFFER == \#* ]]; then
        # Extract command after #
        local cmd="${BUFFER#\#}"
        # Execute hashcli with the command
        hashcli $cmd
        # Clear buffer
        BUFFER=""
        # Redraw prompt
        zle reset-prompt
    else
        # Execute normal command
        zle .accept-line
    fi
}

# Register the widget
zle -N hash-magic-execute

# Bind to Enter key
bindkey '^M' hash-magic-execute

# Add completion for hashcli
which _hashcli >/dev/null 2>&1 && _hashcli
EOF

# Set up fish integration
echo "Setting up fish integration..."
cat > ~/.config/fish/config.fish << 'EOF'
# Hashcmd fish integration
function hashcli_intercept --on-event fish_preexec
    set -l cmd (commandline)
    if string match -q '#*' $cmd
        # Extract command after #
        set -l hashcli (string sub -s 2 $cmd)
        # Execute hashcli with the command
        hashcli $hashcli
        # Clear commandline
        commandline -r ""
        # Cancel execution of the original command
        commandline -f repaint
        return 1
    end
end

# Fish completion for hashcli (basic)
complete -c hashcli -f -a "(__fish_complete_subcommand)"
EOF

echo "Shell integrations setup complete!"
echo ""
echo "To test the integrations:"
echo "1. Run 'docker-compose exec hashcli-test-env zsh' for zsh"
echo "2. Run 'docker-compose exec hashcli-test-env fish' for fish"
echo "3. Try using '#' prefix in the shell (e.g., '# how do I list files?')"
