#!/bin/bash
# zsh Installation Script for Hash CLI Integration
# This script installs the Hash CLI zsh integration

set -e

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOME}/.config/zsh/hash"
COMPLETIONS_DIR="${INSTALL_DIR}/completions"
BACKUP_DIR="${HOME}/.config/zsh/hash/backup"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if hashcli is available
check_hashcli() {
    if ! command -v hashcli >/dev/null 2>&1; then
        log_warning "hashcli command not found in PATH."
        log_info "Please ensure Hash CLI is installed before using the shell integration."
        log_info "You can install it with: pip install hashcli"
        return 1
    fi
    return 0
}

# Create backup of existing configuration
create_backup() {
    local file="$1"
    local backup_name="$(basename "$file").backup.$(date +%Y%m%d_%H%M%S)"
    
    if [[ -f "$file" ]]; then
        mkdir -p "$BACKUP_DIR"
        cp "$file" "$BACKUP_DIR/$backup_name"
        log_info "Backed up $file to $BACKUP_DIR/$backup_name"
    fi
}

# Install hash integration files
install_files() {
    log_info "Installing Hash zsh integration files..."
    
    # Create directories
    mkdir -p "$INSTALL_DIR" "$COMPLETIONS_DIR"
    
    # Copy integration files
    if [[ -f "$SCRIPT_DIR/hash.zsh" ]]; then
        cp "$SCRIPT_DIR/hash.zsh" "$INSTALL_DIR/"
        chmod +x "$INSTALL_DIR/hash.zsh"
        log_success "Installed hash.zsh"
    else
        log_error "hash.zsh not found in $SCRIPT_DIR"
        exit 1
    fi
    
    if [[ -f "$SCRIPT_DIR/_hash" ]]; then
        cp "$SCRIPT_DIR/_hash" "$COMPLETIONS_DIR/"
        chmod +r "$COMPLETIONS_DIR/_hash"
        log_success "Installed _hash completion"
    else
        log_error "_hash completion file not found in $SCRIPT_DIR"
        exit 1
    fi
}

# Update zsh configuration
update_zshrc() {
    local zshrc="$HOME/.zshrc"
    local hash_source_line="source ~/.config/zsh/hash/hash.zsh"
    local fpath_line='fpath=(~/.config/zsh/hash/completions $fpath)'
    local compinit_line="autoload -Uz compinit && compinit"
    
    log_info "Updating zsh configuration..."
    
    # Create backup
    create_backup "$zshrc"
    
    # Ensure .zshrc exists
    touch "$zshrc"
    
    # Add fpath configuration
    if ! grep -q "fpath=.*hash/completions" "$zshrc" 2>/dev/null; then
        echo "" >> "$zshrc"
        echo "# Hash CLI completion path" >> "$zshrc"
        echo "$fpath_line" >> "$zshrc"
        log_success "Added completion path to .zshrc"
    else
        log_info "Completion path already exists in .zshrc"
    fi
    
    # Add compinit if not present
    if ! grep -q "compinit" "$zshrc" 2>/dev/null; then
        echo "$compinit_line" >> "$zshrc"
        log_success "Added compinit to .zshrc"
    fi
    
    # Add hash integration source
    if ! grep -q "hash/hash.zsh" "$zshrc" 2>/dev/null; then
        echo "" >> "$zshrc"
        echo "# Hash CLI integration" >> "$zshrc"
        echo "$hash_source_line" >> "$zshrc"
        log_success "Added Hash integration to .zshrc"
    else
        log_info "Hash integration already exists in .zshrc"
    fi
}

# Verify installation
verify_installation() {
    log_info "Verifying installation..."
    
    local files_to_check=(
        "$INSTALL_DIR/hash.zsh"
        "$COMPLETIONS_DIR/_hash"
    )
    
    for file in "${files_to_check[@]}"; do
        if [[ -f "$file" ]]; then
            log_success "✓ $file"
        else
            log_error "✗ $file (missing)"
            return 1
        fi
    done
    
    # Check if .zshrc was updated
    if grep -q "hash/hash.zsh" "$HOME/.zshrc" 2>/dev/null; then
        log_success "✓ .zshrc configuration"
    else
        log_error "✗ .zshrc configuration (missing)"
        return 1
    fi
    
    return 0
}

# Uninstall function
uninstall() {
    log_info "Uninstalling Hash zsh integration..."
    
    # Remove files
    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
        log_success "Removed installation directory"
    fi
    
    # Remove from .zshrc
    local zshrc="$HOME/.zshrc"
    if [[ -f "$zshrc" ]]; then
        create_backup "$zshrc"
        
        # Remove hash-related lines
        sed -i.tmp '/# Hash CLI/d; /hash\/hash.zsh/d; /hash\/completions/d' "$zshrc"
        rm -f "$zshrc.tmp"
        
        log_success "Removed Hash integration from .zshrc"
    fi
    
    log_success "Hash zsh integration uninstalled successfully!"
    log_info "Please restart your shell or run: source ~/.zshrc"
}

# Show usage information
show_usage() {
    echo "Hash CLI zsh Integration Installer"
    echo ""
    echo "Usage: $0 [OPTION]"
    echo ""
    echo "Options:"
    echo "  install     Install Hash zsh integration (default)"
    echo "  uninstall   Remove Hash zsh integration"
    echo "  verify      Verify installation"
    echo "  help        Show this help message"
    echo ""
    echo "After installation, the following features will be available:"
    echo "  • Type '#' followed by your query to use Hash CLI"
    echo "  • Tab completion for both LLM and command proxy modes"
    echo "  • Seamless integration with existing zsh workflow"
    echo ""
    echo "Examples:"
    echo "  # how do I list files recursively?"
    echo "  # /ls -la"
    echo "  # /model gpt-4"
}

# Main installation function
main_install() {
    log_info "Starting Hash CLI zsh integration installation..."
    echo ""
    
    # Check prerequisites
    if ! command -v zsh >/dev/null 2>&1; then
        log_error "zsh is not installed or not in PATH"
        exit 1
    fi
    
    # Check hashcli availability (warning only)
    check_hashcli || log_warning "Installation will continue, but Hash CLI should be installed for full functionality"
    
    # Install files and update configuration
    install_files
    update_zshrc
    
    # Verify installation
    if verify_installation; then
        echo ""
        log_success "Hash zsh integration installed successfully!"
        echo ""
        log_info "To start using Hash CLI integration:"
        log_info "1. Restart your shell or run: source ~/.zshrc"
        log_info "2. Try typing: # /help"
        log_info "3. Or ask a question: # how do I list files?"
        echo ""
        log_info "Use 'hash-toggle' command to enable/disable integration"
    else
        log_error "Installation verification failed!"
        exit 1
    fi
}

# Main script logic
case "${1:-install}" in
    install)
        main_install
        ;;
    uninstall)
        uninstall
        ;;
    verify)
        verify_installation && log_success "Installation verified successfully!" || log_error "Installation verification failed!"
        ;;
    help|--help|-h)
        show_usage
        ;;
    *)
        log_error "Unknown option: $1"
        show_usage
        exit 1
        ;;
esac
