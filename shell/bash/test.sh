#!/bin/bash
# Test script for Hash CLI bash integration
# This script validates the bash integration installation

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
}

log_error() {
    echo -e "${RED}[FAIL]${NC} $1"
}

# Test execution wrapper
run_test() {
    local test_name="$1"
    local test_command="$2"

    TESTS_RUN=$((TESTS_RUN + 1))
    echo ""
    log_info "Running test: $test_name"

    if eval "$test_command"; then
        log_success "$test_name"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    else
        log_error "$test_name"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        return 1
    fi
}

# Test functions
test_bash_available() {
    command -v bash >/dev/null 2>&1
}

test_hashcli_available() {
    command -v hashcli >/dev/null 2>&1
}

test_integration_files_exist() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    [[ -f "$script_dir/hash.bash" ]] && \
    [[ -f "$script_dir/hash_completion.bash" ]] && \
    [[ -f "$script_dir/install.sh" ]]
}

test_install_script_executable() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    [[ -x "$script_dir/install.sh" ]]
}

test_hash_bash_syntax() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    bash -n "$script_dir/hash.bash"
}

test_completion_bash_syntax() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    bash -n "$script_dir/hash_completion.bash"
}

test_install_script_syntax() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    bash -n "$script_dir/install.sh"
}

test_hash_functions_defined() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    source "$script_dir/hash.bash"
    declare -f hash_magic_execute >/dev/null 2>&1 && \
    declare -f hash_check_availability >/dev/null 2>&1 && \
    declare -f hash_toggle >/dev/null 2>&1
}

test_completion_functions_defined() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    source "$script_dir/hash_completion.bash"
    declare -f _hash_completion >/dev/null 2>&1 && \
    declare -f _hash_command_proxy_mode >/dev/null 2>&1 && \
    declare -f _hash_llm_mode >/dev/null 2>&1
}

test_install_help_works() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    "$script_dir/install.sh" help >/dev/null 2>&1
}

# Print test summary
print_summary() {
    echo ""
    echo "========================================"
    echo "Test Summary"
    echo "========================================"
    echo "Total tests run: $TESTS_RUN"
    echo -e "Tests passed:    ${GREEN}$TESTS_PASSED${NC}"
    echo -e "Tests failed:    ${RED}$TESTS_FAILED${NC}"
    echo ""

    if [[ $TESTS_FAILED -eq 0 ]]; then
        log_success "All tests passed!"
        return 0
    else
        log_error "Some tests failed!"
        return 1
    fi
}

# Main test execution
main() {
    echo "========================================"
    echo "Hash CLI bash Integration Test Suite"
    echo "========================================"

    # Run tests
    run_test "Bash is available" "test_bash_available"
    run_test "Integration files exist" "test_integration_files_exist"
    run_test "Install script is executable" "test_install_script_executable"
    run_test "hash.bash has valid syntax" "test_hash_bash_syntax"
    run_test "hash_completion.bash has valid syntax" "test_completion_bash_syntax"
    run_test "install.sh has valid syntax" "test_install_script_syntax"
    run_test "Hash functions are defined correctly" "test_hash_functions_defined"
    run_test "Completion functions are defined correctly" "test_completion_functions_defined"
    run_test "Install script help works" "test_install_help_works"

    # Optional tests (warnings only)
    if ! test_hashcli_available; then
        echo ""
        log_info "Note: hashcli command not found (optional for testing)"
    fi

    # Print summary
    print_summary
}

# Run main function
main
exit $?
