#!/bin/bash
# Basic test script for Hash fish integration

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Hash fish Integration Test ==="
echo ""

# Test 1: Check if files exist
echo "Test 1: Checking installation files..."
files_to_check=(
    "$SCRIPT_DIR/hash.fish"
    "$SCRIPT_DIR/completions.fish"
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

if [[ -r "$SCRIPT_DIR/hash.fish" ]]; then
    echo "✓ hash.fish is readable"
else
    echo "✗ hash.fish is not readable"
    exit 1
fi

if [[ -r "$SCRIPT_DIR/completions.fish" ]]; then
    echo "✓ completions.fish is readable"
else
    echo "✗ completions.fish is not readable"
    exit 1
fi
echo "✅ File permissions OK"
echo ""

# Test 3: Syntax check for fish files
echo "Test 3: Checking fish syntax..."
if fish -n "$SCRIPT_DIR/hash.fish" 2>/dev/null; then
    echo "✓ hash.fish syntax is valid"
else
    echo "✗ hash.fish has syntax errors"
    exit 1
fi
if fish -n "$SCRIPT_DIR/completions.fish" 2>/dev/null; then
    echo "✓ completions.fish syntax is valid"
else
    echo "✗ completions.fish has syntax errors"
    exit 1
fi
echo "✅ fish syntax OK"
echo ""

# Test 4: Check hashcli availability
echo "Test 4: Checking hashcli availability..."
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
echo "✅ Hash fish integration files are properly structured"
echo "✅ All core files exist with correct permissions"
echo "✅ fish syntax validation passed"
echo ""
echo "🎉 Basic integration tests passed!"
echo ""
echo "Next steps for full testing:"
echo "1. Run: ./install.sh install"
echo "2. Restart fish"
echo "3. Test: # /help"
echo "4. Test: # how do I list files?"
