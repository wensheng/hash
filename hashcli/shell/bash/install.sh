#!/bin/bash
# bash Installation Script for Hash CLI Integration
# This script installs the Hash CLI bash integration

set -e

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOME}/.config/bash/hash"
BACKUP_DIR="${HOME}/.config/bash/hash/backup"

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
    log_info "Installing Hash bash integration files..."

    # Create directories
    mkdir -p "$INSTALL_DIR"

    # Copy integration files
    if [[ -f "$SCRIPT_DIR/hash.bash" ]]; then
        cp "$SCRIPT_DIR/hash.bash" "$INSTALL_DIR/"
        chmod +x "$INSTALL_DIR/hash.bash"
        log_success "Installed hash.bash"
    else
        log_error "hash.bash not found in $SCRIPT_DIR"
        exit 1
    fi

    if [[ -f "$SCRIPT_DIR/hash_completion.bash" ]]; then
        cp "$SCRIPT_DIR/hash_completion.bash" "$INSTALL_DIR/"
        chmod +r "$INSTALL_DIR/hash_completion.bash"
        log_success "Installed hash_completion.bash"
    else
        log_error "hash_completion.bash not found in $SCRIPT_DIR"
        exit 1
    fi
}

# Update bash configuration
update_bashrc() {
    local bashrc="$HOME/.bashrc"
    local hash_source_line="source ~/.config/bash/hash/hash.bash"

    log_info "Updating bash configuration..."

    # Create backup
    create_backup "$bashrc"

    # Ensure .bashrc exists
    touch "$bashrc"

    # Add hash integration source
    if ! grep -q "bash/hash/hash.bash" "$bashrc" 2>/dev/null; then
        echo "" >> "$bashrc"
        echo "# Hash CLI integration" >> "$bashrc"
        echo "$hash_source_line" >> "$bashrc"
        log_success "Added Hash integration to .bashrc"
    else
        log_info "Hash integration already exists in .bashrc"
    fi
}

# Verify installation
verify_installation() {
    log_info "Verifying installation..."

    local files_to_check=(
        "$INSTALL_DIR/hash.bash"
        "$INSTALL_DIR/hash_completion.bash"
    )

    for file in "${files_to_check[@]}"; do
        if [[ -f "$file" ]]; then
            log_success "✓ $file"
        else
            log_error "✗ $file (missing)"
            return 1
        fi
    done

    # Check if .bashrc was updated
    if grep -q "bash/hash/hash.bash" "$HOME/.bashrc" 2>/dev/null; then
        log_success "✓ .bashrc configuration"
    else
        log_error "✗ .bashrc configuration (missing)"
        return 1
    fi

    return 0
}

# Uninstall function
uninstall() {
    log_info "Uninstalling Hash bash integration..."

    # Remove files
    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
        log_success "Removed installation directory"
    fi

    # Remove from .bashrc
    local bashrc="$HOME/.bashrc"
    if [[ -f "$bashrc" ]]; then
        create_backup "$bashrc"

        # Remove hash-related lines
        sed -i.tmp '/# Hash CLI integration/d; /bash\/hash\/hash.bash/d' "$bashrc"
        rm -f "$bashrc.tmp"

        log_success "Removed Hash integration from .bashrc"
    fi

    log_success "Hash bash integration uninstalled successfully!"
    log_info "Please restart your shell or run: source ~/.bashrc"
}

# Show usage information
show_usage() {
    echo "Hash CLI bash Integration Installer"
    echo ""
    echo "Usage: $0 [OPTION]"
    echo ""
    echo "Options:"
    echo "  install     Install Hash bash integration (default)"
    echo "  uninstall   Remove Hash bash integration"
    echo "  verify      Verify installation"
    echo "  help        Show this help message"
    echo ""
    echo "After installation, the following features will be available:"
    echo "  • Type '#' followed by your query to use Hash CLI"
    echo "  • Tab completion for both LLM and command proxy modes"
    echo "  • Seamless integration with existing bash workflow"
    echo ""
    echo "Examples:"
    echo "  # how do I list files recursively?"
    echo "  # /ls -la"
    echo "  # /model gpt-4"
}

# Main installation function
main_install() {
    log_info "Starting Hash CLI bash integration installation..."
    echo ""

    # Check prerequisites
    if [[ -z "$BASH_VERSION" ]]; then
        log_error "This script must be run with bash"
        exit 1
    fi

    # Check hashcli availability (warning only)
    check_hashcli || log_warning "Installation will continue, but Hash CLI should be installed for full functionality"

    # Install files and update configuration
    install_files
    update_bashrc

    # Verify installation
    if verify_installation; then
        echo ""
        log_success "Hash bash integration installed successfully!"
        echo ""
        log_info "To start using Hash CLI integration:"
        log_info "1. Restart your shell or run: source ~/.bashrc"
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
