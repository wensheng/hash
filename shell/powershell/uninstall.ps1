# Hash CLI PowerShell Integration Uninstaller
#Requires -Version 5.1

[CmdletBinding()]
param(
    [string]$InstallPath = "$env:USERPROFILE\.hashcli\powershell",
    [switch]$RemoveConfig,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

$ProfileLine = 'Import-Module "$env:USERPROFILE\.hashcli\powershell\hash.ps1" -Force'

function Write-Status {
    param([string]$Message, [string]$Status = 'Info')

    $color = switch ($Status) {
        'Success' { 'Green' }
        'Warning' { 'Yellow' }
        'Error' { 'Red' }
        default { 'White' }
    }

    Write-Host "[$Status] $Message" -ForegroundColor $color
}

function Remove-HashFiles {
    Write-Status "Removing Hash integration files..."

    if (Test-Path $InstallPath) {
        if (-not $WhatIf) {
            Remove-Item -Path $InstallPath -Recurse -Force
        }
        Write-Status "Removed directory: $InstallPath" -Status 'Success'
    } else {
        Write-Status "Installation directory not found: $InstallPath" -Status 'Warning'
    }
}

function Update-PowerShellProfile {
    Write-Status "Updating PowerShell profile..."

    $profilePath = $PROFILE.CurrentUserAllHosts

    if (-not (Test-Path $profilePath)) {
        Write-Status "PowerShell profile not found" -Status 'Warning'
        return
    }

    $profileContent = Get-Content $profilePath
    $updatedContent = $profileContent | Where-Object {
        $_ -ne $ProfileLine -and $_ -ne "# Hash CLI Integration"
    }

    if ($profileContent.Count -ne $updatedContent.Count) {
        if (-not $WhatIf) {
            $updatedContent | Set-Content -Path $profilePath
        }
        Write-Status "Removed Hash integration from PowerShell profile" -Status 'Success'
    } else {
        Write-Status "Hash integration not found in profile" -Status 'Warning'
    }
}

function Remove-Configuration {
    if ($RemoveConfig) {
        Write-Status "Removing Hash configuration..."

        $configDir = "$env:USERPROFILE\.hashcli"
        if (Test-Path $configDir) {
            if (-not $WhatIf) {
                Remove-Item -Path $configDir -Recurse -Force
            }
            Write-Status "Removed configuration directory: $configDir" -Status 'Success'
        }
    }
}

# Main uninstallation process
try {
    Write-Status "Starting Hash CLI PowerShell integration removal..."

    if ($WhatIf) {
        Write-Status "Running in WhatIf mode - no changes will be made" -Status 'Warning'
    }

    Remove-HashFiles
    Update-PowerShellProfile
    Remove-Configuration

    Write-Status "Uninstallation completed successfully!" -Status 'Success'
    Write-Status "Please restart PowerShell to complete the removal process" -Status 'Info'

} catch {
    Write-Status "Uninstallation failed: $_" -Status 'Error'
    exit 1
}
