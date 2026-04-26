# Hash completions for fish shell

# Function to detect mode and provide appropriate completions
function __hash_complete_slash_commands
    set -l commands (hashcli --completion-commands 2>/dev/null)
    if test (count $commands) -eq 0
        echo -e "help\tShow available commands"
        echo -e "history\tManage conversation history"
        echo -e "config\tManage configuration"
        return
    end
    for entry in $commands
        set -l parts (string split \t -- $entry)
        echo -e "$parts[1]\t$parts[2]"
    end
end

function __hash_complete_command
    set -l buffer_content (commandline -cp)

    # Remove # prefix and leading whitespace
    set buffer_content (string replace -r '^#\s*' '' $buffer_content)

    # Mode detection
    if string match -q '/*' "$buffer_content"
        # Command proxy mode
        set -l proxy_cmd (string replace -r '^/([^[:space:]]+).*' '$1' "$buffer_content")

        switch "$proxy_cmd"
            case history
                echo -e "list\tList recent conversations"
                echo -e "show\tShow a conversation by ID"
                echo -e "search\tSearch conversation history"
                echo -e "clear\tClear all history"
            case config
                echo -e "get\tShow a config value"
                echo -e "set\tSet a config value"
                echo -e "unset\tRemove a config value"
            case '*'
                __hash_complete_slash_commands
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
