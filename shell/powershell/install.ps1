# Hash CLI PowerShell Integration Installer
#Requires -Version 5.1

[CmdletBinding()]
param(
    [string]$InstallPath = "$env:USERPROFILE\.hashcli\powershell",
    [switch]$Force,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

# Configuration
$ScriptFiles = @(
    'hash.ps1',
    'completions.ps1'
)

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

function Test-Prerequisites {
    Write-Status "Checking prerequisites..."

    # Check PowerShell version
    if ($PSVersionTable.PSVersion.Major -lt 5) {
        throw "PowerShell 5.1 or higher is required"
    }
    Write-Status "PowerShell version: $($PSVersionTable.PSVersion)" -Status 'Success'

    # Check for PSReadLine
    if (-not (Get-Module -ListAvailable PSReadLine)) {
        Write-Status "PSReadLine module not found. Installing..." -Status 'Warning'
        Install-Module PSReadLine -Force -Scope CurrentUser
    }
    Write-Status "PSReadLine module available" -Status 'Success'

    # Check for hashcli executable
    try {
        $version = hashcli --version 2>$null
        Write-Status "hashcli found: $version" -Status 'Success'
    } catch {
        Write-Status "hashcli executable not found in PATH. Please install hashcli first." -Status 'Warning'
    }
}

function Install-HashFiles {
    Write-Status "Installing Hash integration files..."

    # Create installation directory
    if (-not (Test-Path $InstallPath)) {
        if (-not $WhatIf) {
            New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
        }
        Write-Status "Created directory: $InstallPath" -Status 'Success'
    }

    # Copy script files
    $scriptDir = Split-Path $MyInvocation.MyCommand.Path -Parent

    foreach ($file in $ScriptFiles) {
        $sourcePath = Join-Path $scriptDir $file
        $targetPath = Join-Path $InstallPath $file

        if (Test-Path $sourcePath) {
            if (-not $WhatIf) {
                Copy-Item -Path $sourcePath -Destination $targetPath -Force
            }
            Write-Status "Copied $file" -Status 'Success'
        } else {
            Write-Status "Source file not found: $sourcePath" -Status 'Error'
        }
    }
}

function Update-PowerShellProfile {
    Write-Status "Updating PowerShell profile..."

    $profilePath = $PROFILE.CurrentUserAllHosts

    # Create profile if it doesn't exist
    if (-not (Test-Path $profilePath)) {
        if (-not $WhatIf) {
            New-Item -ItemType File -Path $profilePath -Force | Out-Null
        }
        Write-Status "Created PowerShell profile: $profilePath" -Status 'Success'
    }

    # Check if already configured
    $profileContent = Get-Content $profilePath -ErrorAction SilentlyContinue
    if ($profileContent -contains $ProfileLine) {
        Write-Status "Hash integration already configured in profile" -Status 'Warning'
        return
    }

    # Add to profile
    if (-not $WhatIf) {
        Add-Content -Path $profilePath -Value "`n# Hash CLI Integration"
        Add-Content -Path $profilePath -Value $ProfileLine
    }

    Write-Status "Added Hash integration to PowerShell profile" -Status 'Success'
}

function Test-Installation {
    Write-Status "Testing installation..."

    try {
        # Import the module
        Import-Module "$InstallPath\hash.ps1" -Force

        # Test the integration
        if (Test-HashIntegration) {
            Write-Status "Installation test passed" -Status 'Success'
        } else {
            Write-Status "Installation test failed" -Status 'Error'
        }
    } catch {
        Write-Status "Installation test error: $_" -Status 'Error'
    }
}

# Main installation process
try {
    Write-Status "Starting Hash CLI PowerShell integration installation..."

    if ($WhatIf) {
        Write-Status "Running in WhatIf mode - no changes will be made" -Status 'Warning'
    }

    Test-Prerequisites
    Install-HashFiles
    Update-PowerShellProfile

    if (-not $WhatIf) {
        Test-Installation
    }

    Write-Status "Installation completed successfully!" -Status 'Success'
    Write-Status "Please restart PowerShell or run: Import-Module `"$InstallPath\hash.ps1`" -Force" -Status 'Info'

} catch {
    Write-Status "Installation failed: $_" -Status 'Error'
    exit 1
}
