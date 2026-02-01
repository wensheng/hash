#!/bin/bash

# Test script for hashcli Docker environment

echo "Testing hashcli installation..."

# Test basic installation
echo "1. Testing hcli command availability:"
if command -v hcli &> /dev/null; then
    echo "   ✓ hcli command is available"
else
    echo "   ✗ hcli command is not available"
    exit 1
fi

# Test version command
echo "2. Testing hcli version:"
if hcli --version &> /dev/null; then
    echo "   ✓ hcli version command works"
    hcli --version
else
    echo "   ✗ hcli version command failed"
fi

# Test help command
echo "3. Testing hcli help:"
if hcli --help &> /dev/null; then
    echo "   ✓ hcli help command works"
else
    echo "   ✗ hcli help command failed"
fi

# Test Python import
echo "4. Testing Python module import:"
if python3 -c "import hashcli; print('   ✓ hashcli module imported successfully')"; then
    echo "   ✓ hashcli Python module import works"
else
    echo "   ✗ hashcli Python module import failed"
fi

echo "Basic tests completed!"
