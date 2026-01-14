# Hash integration for fish shell
function hash_magic_execute --description "Execute hash commands with # prefix"
    set -l current_buffer (commandline)

    # Check if buffer starts with #
    if string match -q '#*' "$current_buffer"
        # Extract command after #
        set -l cmd (string replace -r '^#\s*' '' "$current_buffer")

        if test -n "$cmd"
            echo  # New line
            hashcli $cmd
            echo  # Another new line
        end

        # Add to shell history
        if test -n "$current_buffer"
            history --add -- "$current_buffer"
        end

        # Clear the command line
        commandline ""
    else
        # Normal command execution
        commandline -f execute
    end
end

# Bind to Enter key
bind \r hash_magic_execute
bind \n hash_magic_execute
