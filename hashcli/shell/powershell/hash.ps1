# Hash CLI Integration for PowerShell
# Requires PowerShell 5.1+ or PowerShell Core 7+

# Check if PSReadLine module is available
if (-not (Get-Module -ListAvailable PSReadLine)) {
    Write-Warning "PSReadLine module is required for Hash integration"
    return
}

# Import required modules
Import-Module PSReadLine -Force

# Source the completions script
. (Join-Path $PSScriptRoot 'completions.ps1')

# Global variables for Hash integration
$HashConfig = @{
    ExecutablePath = "hashcli"
    HistoryFile = "$env:USERPROFILE\.hashcli\powershell_history.json"
    EnableLogging = $false
}

# Function to handle # prefix detection and execution
function Invoke-HashCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Buffer,
        [int]$CursorPosition
    )

    # Check if buffer starts with #
    if ($Buffer -match '^\s*#\s*(.*)$') {
        $command = $Matches[1].Trim()

        if ($command) {
            try {
                Write-Host ""  # New line for output

                # Execute hashcli with the command
                $result = & $HashConfig.ExecutablePath $command 2>&1

                if ($LASTEXITCODE -eq 0) {
                    Write-Host $result
                } else {
                    Write-Error "Hash command failed: $result"
                }

                Write-Host ""  # Another new line

                # Log command if enabled
                if ($HashConfig.EnableLogging) {
                    Add-HashCommandToHistory -Command $command -Timestamp (Get-Date)
                }

            } catch {
                Write-Error "Failed to execute hash command: $_"
            }
        }

        # Clear the current line
        [Microsoft.PowerShell.PSConsoleReadLine]::ClearLine()
        return $true
    }

    return $false
}

# Custom Enter key handler
function Set-HashEnterKeyHandler {
    Set-PSReadlineKeyHandler -Key Enter -ScriptBlock {
        $line = $null
        $cursor = $null
        [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)

        # Try to handle as Hash command
        if (Invoke-HashCommand -Buffer $line -CursorPosition $cursor) {
            return  # Hash command was handled
        }

        # Not a Hash command, execute normally
        [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
    }
}

# History management functions
function Add-HashCommandToHistory {
    [CmdletBinding()]
    param(
        [string]$Command,
        [datetime]$Timestamp
    )

    $historyEntry = @{
        Command = $Command
        Timestamp = $Timestamp.ToString("yyyy-MM-ddTHH:mm:ss")
        SessionId = $PID
    }

    $historyDir = Split-Path $HashConfig.HistoryFile -Parent
    if (-not (Test-Path $historyDir)) {
        New-Item -ItemType Directory -Path $historyDir -Force | Out-Null
    }

    $history = @()
    if (Test-Path $HashConfig.HistoryFile) {
        $history = Get-Content $HashConfig.HistoryFile | ConvertFrom-Json
    }

    $history += $historyEntry

    # Keep only last 1000 entries
    if ($history.Count -gt 1000) {
        $history = $history[-1000..-1]
    }

    $history | ConvertTo-Json | Set-Content $HashConfig.HistoryFile
}

# Configuration functions
function Set-HashConfig {
    [CmdletBinding()]
    param(
        [string]$ExecutablePath,
        [string]$HistoryFile,
        [bool]$EnableLogging
    )

    if ($ExecutablePath) { $HashConfig.ExecutablePath = $ExecutablePath }
    if ($HistoryFile) { $HashConfig.HistoryFile = $HistoryFile }
    if ($PSBoundParameters.ContainsKey('EnableLogging')) {
        $HashConfig.EnableLogging = $EnableLogging
    }
}

function Get-HashConfig {
    return $HashConfig
}

# Verification function
function Test-HashIntegration {
    [CmdletBinding()]
    param()

    Write-Host "Testing Hash CLI integration..."

    # Check if hashcli is available
    try {
        $version = & $HashConfig.ExecutablePath --version 2>&1
        Write-Host "✓ hashcli executable found: $version" -ForegroundColor Green
    } catch {
        Write-Host "✗ hashcli executable not found" -ForegroundColor Red
        return $false
    }

    # Check PSReadLine integration
    try {
        $handlers = Get-PSReadlineKeyHandler | Where-Object { $_.Key -eq "Enter" }
        if ($handlers) {
            Write-Host "✓ Enter key handler is configured" -ForegroundColor Green
        } else {
            Write-Host "✗ Enter key handler not found" -ForegroundColor Red
            return $false
        }
    } catch {
        Write-Host "✗ Error checking key handlers: $_" -ForegroundColor Red
        return $false
    }

    Write-Host "Hash integration test completed successfully!" -ForegroundColor Green
    return $true
}

# Initialize the integration
function Initialize-HashIntegration {
    [CmdletBinding()]
    param()

    try {
        # Set up the Enter key handler
        Set-HashEnterKeyHandler

        Write-Verbose "Hash CLI integration initialized successfully"
        return $true
    } catch {
        Write-Error "Failed to initialize Hash integration: $_"
        return $false
    }
}

# Export functions
Export-ModuleMember -Function @(
    'Set-HashConfig',
    'Get-HashConfig',
    'Test-HashIntegration',
    'Initialize-HashIntegration'
)

# Auto-initialize if not in testing mode
if (-not $env:HASH_TESTING_MODE) {
    Initialize-HashIntegration
}
