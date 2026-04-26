# Hash CLI Completions for PowerShell

# Completion script block
$HashCompletionScript = {
    param($commandName, $parameterName, $wordToComplete, $commandAst, $fakeBoundParameters)

    # Get the full command line
    $line = $commandAst.CommandElements[0].Value

    # Extract content after # prefix
    if ($line -match '^\s*#\s*(.*)$') {
        $hashCommand = $Matches[1]

        # Mode detection
        if ($hashCommand -match '^\s*/') {
            # Command proxy mode
            Complete-HashProxyCommand -Command $hashCommand -WordToComplete $wordToComplete
        } else {
            # LLM mode
            Complete-HashLLMQuery -Query $hashCommand -WordToComplete $wordToComplete
        }
    }
}

# Completion for command proxy mode
function Complete-HashProxyCommand {
    [CmdletBinding()]
    param(
        [string]$Command,
        [string]$WordToComplete
    )

    # Extract the proxy command
    $proxyCmd = ($Command -replace '^\s*/([^\s]+).*', '$1').Trim()

    switch ($proxyCmd) {
        'config' {
            # Configuration options
            $configOptions = @('get', 'set', 'unset')
            $configOptions | Where-Object { $_ -like "*$WordToComplete*" } | ForEach-Object {
                [System.Management.Automation.CompletionResult]::new(
                    $_,
                    $_,
                    [System.Management.Automation.CompletionResultType]::ParameterValue,
                    "Configuration option: $_"
                )
            }
        }

        'history' {
            $historyOptions = @('list', 'show', 'search', 'clear')
            $historyOptions | Where-Object { $_ -like "*$WordToComplete*" } | ForEach-Object {
                [System.Management.Automation.CompletionResult]::new(
                    $_,
                    $_,
                    [System.Management.Automation.CompletionResultType]::ParameterValue,
                    "History option: $_"
                )
            }
        }

        default {
            $commands = Get-HashSlashCommands
            $commands | Where-Object { $_.Name -like "*$WordToComplete*" } | ForEach-Object {
                [System.Management.Automation.CompletionResult]::new(
                    $_.Name,
                    $_.Name,
                    [System.Management.Automation.CompletionResultType]::ParameterValue,
                    $_.Description
                )
            }
        }
    }
}

function Get-HashSlashCommands {
    $fallback = @(
        @{Name='help'; Description='Show available commands'},
        @{Name='history'; Description='Manage conversation history'},
        @{Name='config'; Description='Manage configuration'}
    )

    try {
        $rows = hashcli --completion-commands 2>$null
        if (-not $rows) {
            return $fallback
        }
        return $rows | ForEach-Object {
            $parts = $_ -split "`t", 2
            $description = ''
            if ($parts.Length -gt 1) {
                $description = $parts[1]
            }
            @{Name=$parts[0]; Description=$description}
        }
    } catch {
        return $fallback
    }
}

# Completion for LLM mode
function Complete-HashLLMQuery {
    [CmdletBinding()]
    param(
        [string]$Query,
        [string]$WordToComplete
    )

    # Common query patterns and suggestions
    $suggestions = @(
        @{Text='how do I'; Description='General how-to questions'},
        @{Text='explain this error:'; Description='Error explanation and troubleshooting'},
        @{Text='help me with'; Description='General assistance requests'},
        @{Text='troubleshoot'; Description='System and application troubleshooting'},
        @{Text='optimize'; Description='Performance optimization advice'},
        @{Text='find files'; Description='File and directory search help'},
        @{Text='fix permission issues'; Description='Permission and access problems'},
        @{Text='network connectivity'; Description='Network troubleshooting'},
        @{Text='git commands'; Description='Git version control help'},
        @{Text='powershell scripting'; Description='PowerShell development assistance'},
        @{Text='windows administration'; Description='Windows system administration'},
        @{Text='security best practices'; Description='Security recommendations'},
        @{Text='performance monitoring'; Description='System performance analysis'},
        @{Text='automate task'; Description='Task automation suggestions'}
    )

    # Filter suggestions based on current input
    $suggestions | Where-Object {
        $_.Text -like "*$WordToComplete*" -or $WordToComplete -eq ""
    } | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new(
            $_.Text,
            $_.Text,
            [System.Management.Automation.CompletionResultType]::Text,
            $_.Description
        )
    }
}

# Register the completion
Register-ArgumentCompleter -CommandName '#' -ScriptBlock $HashCompletionScript

# Also register for the pattern-based completion
Register-ArgumentCompleter -Native -CommandName hashcli -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)

    # This handles direct hashcli completion
    $line = $commandAst.CommandElements | ForEach-Object { $_.Value } | Join-String -Separator ' '

    if ($line -match '^hashcli\s+/') {
        # Proxy mode
        Complete-HashProxyCommand -Command ($line -replace '^hashcli\s+', '') -WordToComplete $wordToComplete
    } else {
        # LLM mode
        Complete-HashLLMQuery -Query ($line -replace '^hashcli\s+', '') -WordToComplete $wordToComplete
    }
}
