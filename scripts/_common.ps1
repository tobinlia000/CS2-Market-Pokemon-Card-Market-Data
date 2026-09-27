# Shared helpers for the CS Demo Manager video scripts (Windows PowerShell 5.1+ / PowerShell 7).
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Csdv = Join-Path $RepoRoot 'tools\csdv\csdv.py'

function Get-Csdm {
    $cmd = Get-Command csdm -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\cs-demo-manager\csdm.cmd",
        "$env:ProgramFiles\CS Demo Manager\csdm.cmd",
        "$env:ProgramFiles\cs-demo-manager\csdm.cmd"
    )
    foreach ($path in $candidates) { if (Test-Path $path) { return $path } }
    throw "csdm CLI not found. Install CS Demo Manager (its installer adds csdm to PATH), then open a NEW terminal."
}

function Get-Python {
    foreach ($name in @('py', 'python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike '*WindowsApps*') {
            if ($name -eq 'py') { return @($cmd.Source, '-3') }
            return @($cmd.Source)
        }
    }
    throw "Python 3 not found. Install it with:  winget install Python.Python.3.12   (then open a NEW terminal)."
}

function Invoke-Csdv {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CsdvArgs)
    $py = Get-Python
    $exe = $py[0]
    $pre = @($py | Select-Object -Skip 1)
    # Send output to the host so only the exit code is returned from this function.
    & $exe @pre $Csdv @CsdvArgs | Out-Host
    return $LASTEXITCODE
}
