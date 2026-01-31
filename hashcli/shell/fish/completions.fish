# Hash completions for fish shell

# Function to detect mode and provide appropriate completions
function __hash_complete_command
    set -l buffer_content (commandline -cp)

    # Remove # prefix and leading whitespace
    set buffer_content (string replace -r '^#\s*' '' $buffer_content)

    # Mode detection
    if string match -q '/*' "$buffer_content"
        # Command proxy mode
        set -l proxy_cmd (string replace -r '^/([^[:space:]]+).*' '$1' "$buffer_content")

        switch "$proxy_cmd"
            case clean
                # No additional completions for clean
            case model
                # Available models
                echo -e "gpt-4\tOpenAI GPT-4"
                echo -e "gpt-3.5-turbo\tOpenAI GPT-3.5 Turbo"
                echo -e "claude-3-sonnet\tAnthropic Claude 3 Sonnet"
                echo -e "claude-3-haiku\tAnthropic Claude 3 Haiku"
            case fix
                # Code-related options
                echo -e "bug\tFix a bug"
                echo -e "error\tResolve an error"
                echo -e "performance\tOptimize performance"
                echo -e "security\tAddress security issues"
            case '*'
                # Generic command completions
                __fish_complete_command
        end
    else
        # LLM mode - common query patterns
        echo -e "how do I...\tGeneral how-to questions"
        echo -e "explain this error:\tError explanation"
        echo -e "help me with...\tGeneral assistance"
        echo -e "troubleshoot...\tTroubleshooting help"
        echo -e "optimize...\tOptimization advice"
        echo -e "find files...\tFile search help"
    end
end

# Register completion for # prefix
complete -c '#' -f -a "(__hash_complete_command)"
