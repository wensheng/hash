# Hash integration for fish shell
function hash_magic_execute --description "Execute hash commands for lines containing #"
    set -l current_buffer (commandline)

    # Ignore lines containing '##' and let shell handle comments normally.
    if string match -q '*##*' "$current_buffer"
        commandline -f execute
    # Intercept command line containing '#'
    else if string match -q '*#*' "$current_buffer"
        if test -n "$current_buffer"
            echo  # New line
            hcli "$current_buffer" < /dev/tty
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
