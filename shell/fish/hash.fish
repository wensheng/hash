# Hash integration for fish shell

if not set -q HASHCLI_SESSION_ID
    set -gx HASHCLI_SESSION_ID (uuidgen 2>/dev/null; or python3 -c 'import uuid; print(uuid.uuid4())' 2>/dev/null; or echo "$fish_pid-(date +%s)")
end

function hash_magic_execute --description "Execute hash commands for lines starting with #"
    set -l current_buffer (commandline)
    set -l trimmed_buffer (string trim --left -- "$current_buffer")

    # Keep ## as a plain shell comment escape.
    if string match -q '##*' "$trimmed_buffer"
        commandline -f execute
    # Intercept command lines starting with a single '#'.
    else if string match -q '#*' "$trimmed_buffer"
        if test -n "$current_buffer"
            echo  # New line
            hashcli "$current_buffer" < /dev/tty
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
