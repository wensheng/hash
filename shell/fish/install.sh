#!/bin/bash
# fish Installation Script for Hash Integration

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FISH_CONFIG_DIR="${HOME}/.config/fish"
CONF_D_DIR="${FISH_CONFIG_DIR}/conf.d"
COMPLETIONS_DIR="${FISH_CONFIG_DIR}/completions"

echo "Installing Hash fish integration..."

# Create directories
mkdir -p "$CONF_D_DIR" "$COMPLETIONS_DIR"

# Copy files
cp "$SCRIPT_DIR/hash.fish" "$CONF_D_DIR/hash_integration.fish"
cp "$SCRIPT_DIR/completions.fish" "$COMPLETIONS_DIR/"

echo "Installation complete! Restart fish for changes to take effect."
