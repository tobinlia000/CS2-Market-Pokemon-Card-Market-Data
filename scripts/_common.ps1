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

# --- Safety helpers -------------------------------------------------------------------------------
# HLAE is treated as a cheat by VAC. These helpers make sure recording sessions and real play never mix.

function Get-Cs2GameFolders {
    # Returns every "...\Counter-Strike Global Offensive\game\csgo" folder found in the Steam libraries.
    $steam = (Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction SilentlyContinue).SteamPath
    if (-not $steam) { return @() }
    $libraries = @($steam)
    $vdf = Join-Path $steam 'steamapps\libraryfolders.vdf'
    if (Test-Path $vdf) {
        foreach ($m in [regex]::Matches((Get-Content $vdf -Raw), '"path"\s+"([^"]+)"')) {
            $libraries += ($m.Groups[1].Value -replace '\\\\', '\')
        }
    }
    $libraries | Select-Object -Unique | ForEach-Object {
        Join-Path $_ 'steamapps\common\Counter-Strike Global Offensive\game\csgo'
    } | Where-Object { Test-Path $_ }
}

function Get-RecordingLeftovers {
    # Anything CS:DM / HLAE leave behind that should not be there when you play normally.
    $issues = @()
    foreach ($name in @('cs2', 'hlae')) {
        foreach ($p in @(Get-Process $name -ErrorAction SilentlyContinue)) {
            $issues += "Process running: $($p.ProcessName).exe (pid $($p.Id))"
        }
    }
    foreach ($csgo in Get-Cs2GameFolders) {
        $gameinfo = Join-Path $csgo 'gameinfo.gi'
        if ((Test-Path $gameinfo) -and ((Get-Content $gameinfo -Raw) -match 'csgo/csdm')) {
            $issues += "gameinfo.gi still loads the CS:DM plugin: $gameinfo"
        }
        if (Test-Path "$gameinfo.backup") { $issues += "Leftover gameinfo.gi.backup: $gameinfo.backup" }
        if (Test-Path (Join-Path $csgo 'csdm')) { $issues += "Leftover CS:DM plugin folder: $(Join-Path $csgo 'csdm')" }
    }
    return $issues
}
