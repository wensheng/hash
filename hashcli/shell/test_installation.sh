#!/bin/bash

# Test script for hashcli Docker environment

echo "Testing hashcli installation..."

# Test basic installation
echo "1. Testing hashcli command availability:"
if command -v hashcli &> /dev/null; then
    echo "   ✓ hashcli command is available"
else
    echo "   ✗ hashcli command is not available"
    exit 1
fi

# Test version command
echo "2. Testing hashcli version:"
if hashcli version &> /dev/null; then
    echo "   ✓ hashcli version command works"
    hashcli version
else
    echo "   ✗ hashcli version command failed"
fi

# Test help command
echo "3. Testing hashcli help:"
if hashcli --help &> /dev/null; then
    echo "   ✓ hashcli help command works"
else
    echo "   ✗ hashcli help command failed"
fi

# Test Python import
echo "4. Testing Python module import:"
if python3 -c "import hashcli; print('   ✓ hashcli module imported successfully')"; then
    echo "   ✓ hashcli Python module import works"
else
    echo "   ✗ hashcli Python module import failed"
fi

echo "Basic tests completed!"
