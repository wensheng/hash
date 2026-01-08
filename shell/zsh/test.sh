#!/bin/bash
# Basic test script for Hash zsh integration

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Hash zsh Integration Test ==="
echo ""

# Test 1: Check if files exist
echo "Test 1: Checking installation files..."
files_to_check=(
    "$SCRIPT_DIR/hash.zsh"
    "$SCRIPT_DIR/_hash" 
    "$SCRIPT_DIR/install.sh"
)

all_files_exist=true
for file in "${files_to_check[@]}"; do
    if [[ -f "$file" ]]; then
        echo "✓ $(basename "$file") exists"
    else
        echo "✗ $(basename "$file") missing"
        all_files_exist=false
    fi
done

if [[ "$all_files_exist" == "false" ]]; then
    echo "❌ File existence test failed"
    exit 1
fi
echo "✅ All files exist"
echo ""

# Test 2: Check file permissions
echo "Test 2: Checking file permissions..."
if [[ -x "$SCRIPT_DIR/install.sh" ]]; then
    echo "✓ install.sh is executable"
else
    echo "✗ install.sh is not executable"
    exit 1
fi

if [[ -r "$SCRIPT_DIR/hash.zsh" ]]; then
    echo "✓ hash.zsh is readable"
else
    echo "✗ hash.zsh is not readable"
    exit 1
fi

if [[ -r "$SCRIPT_DIR/_hash" ]]; then
    echo "✓ _hash is readable"
else
    echo "✗ _hash is not readable"
    exit 1
fi
echo "✅ File permissions OK"
echo ""

# Test 3: Syntax check for zsh files
echo "Test 3: Checking zsh syntax..."
if zsh -n "$SCRIPT_DIR/hash.zsh" 2>/dev/null; then
    echo "✓ hash.zsh syntax is valid"
else
    echo "✗ hash.zsh has syntax errors"
    exit 1
fi
echo "✅ zsh syntax OK"
echo ""

# Test 4: Check install script functionality
echo "Test 4: Testing install script..."
if "$SCRIPT_DIR/install.sh" help >/dev/null 2>&1; then
    echo "✓ install.sh help command works"
else
    echo "✗ install.sh help command failed"
    exit 1
fi
echo "✅ Install script basic functionality OK"
echo ""

# Test 5: Check hashcli availability
echo "Test 5: Checking hashcli availability..."
if command -v hashcli >/dev/null 2>&1; then
    echo "✓ hashcli is available in PATH"
    
    # Test hashcli basic functionality
    if hashcli --help >/dev/null 2>&1; then
        echo "✓ hashcli --help works"
    else
        echo "✗ hashcli --help failed"
    fi
else
    echo "⚠ hashcli not found in PATH (expected for development)"
fi
echo ""

echo "=== Test Summary ==="
echo "✅ Hash zsh integration files are properly structured"
echo "✅ All core files exist with correct permissions"
echo "✅ zsh syntax validation passed"
echo "✅ Installation script basic functionality works"
echo ""
echo "🎉 Basic integration tests passed!"
echo ""
echo "Next steps for full testing:"
echo "1. Run: ./install.sh install"
echo "2. Restart zsh or source ~/.zshrc"
echo "3. Test: # /help"
echo "4. Test: # how do I list files?"
