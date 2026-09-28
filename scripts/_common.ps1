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
        if (-not $cmd) { continue }
        # WindowsApps holds both the real Microsoft Store Python and the "install from Store" stub; only the real
        # one answers --version with "Python 3.x".
        if ($cmd.Source -like '*WindowsApps*') {
            $ver = try { & $cmd.Source --version 2>&1 | Out-String } catch { '' }
            if ($ver -notmatch 'Python 3') { continue }
        }
        if ($name -eq 'py') { return @($cmd.Source, '-3') }
        return @($cmd.Source)
    }
    throw "Python 3 not found. Install it with:  winget install Python.Python.3.12   (then open a NEW terminal)."
}

function Invoke-Csdv {
    # Plain function on purpose: a [Parameter()] attribute would make it an advanced function, and PowerShell would
    # then grab csdv flags like -o as its own common parameters (-OutVariable/-OutBuffer).
    $CsdvArgs = $args
    $py = @(Get-Python)   # @() keeps a one-item result as an array (else $py[0] is the first letter)
    $exe = $py[0]
    $pre = @($py | Select-Object -Skip 1)
    # Send output to the host so only the exit code is returned from this function.
    & $exe @pre $Csdv @CsdvArgs | Out-Host
    return $LASTEXITCODE
}

function Get-FFmpeg {
    # The FFmpeg CS:DM is set to use (Settings > Video), else one on PATH.
    $settings = Join-Path $env:USERPROFILE '.csdm\settings.json'
    if (Test-Path $settings) {
        $ff = (Get-Content $settings -Raw | ConvertFrom-Json).video.ffmpegSettings
        if ($ff.customLocationEnabled -and (Test-Path $ff.customExecutableLocation)) { return $ff.customExecutableLocation }
    }
    $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "FFmpeg not found (set it in CS Demo Manager > Settings > Video)."
}

$ReShadeDir = Join-Path $env:USERPROFILE 'Tools\ReShade'

function Set-ReShadePreset {
    # Point ReShade (Tools\ReShade, loaded only by HLAE) at csdv-<name>.ini. No-op when ReShade isn't set up.
    param([string]$Name)
    $ini = Join-Path $ReShadeDir 'ReShade.ini'
    if (-not (Test-Path $ini)) { return }
    $preset = Join-Path $ReShadeDir "csdv-$Name.ini"
    if (-not (Test-Path $preset)) { throw "ReShade preset not found: $preset" }
    $text = [IO.File]::ReadAllText($ini)
    $text = [regex]::Replace($text, '(?m)^PresetPath=.*$', { "PresetPath=.\csdv-$Name.ini" })
    [IO.File]::WriteAllText($ini, $text, (New-Object System.Text.UTF8Encoding $false))
    if ($Name -ne 'off') { Write-Host "ReShade preset: csdv-$Name.ini" -ForegroundColor Cyan }
}

function Join-Sequences {
    # Join a render's sequence files in order (lossless, -c copy) into <Folder>\<Name>.<ext> and move the separate
    # shots into <Folder>\<Name>-shots\. Paths are escaped for FFmpeg's concat list (an apostrophe becomes '\'').
    param([string]$Folder, [string]$Name, $Config)
    $ext = if ($Config.ffmpegSettings.videoContainer) { $Config.ffmpegSettings.videoContainer } else { 'mp4' }
    $files = foreach ($s in ($Config.sequences | Sort-Object number)) {
        $path = Join-Path $Folder ("sequence-{0}-tick-{1}-to-{2}.{3}" -f $s.number, $s.startTick, $s.endTick, $ext)
        if (-not (Test-Path $path)) { throw "Missing rendered shot: $path" }
        $path
    }
    $list = Join-Path ([IO.Path]::GetTempPath()) ("join-" + [guid]::NewGuid() + ".txt")
    $lines = $files | ForEach-Object { "file '" + $_.Replace("'", "'\''") + "'" }
    [IO.File]::WriteAllLines($list, [string[]]$lines, (New-Object System.Text.UTF8Encoding $false))
    $out = Join-Path $Folder "$Name.$ext"
    $ffmpeg = Get-FFmpeg
    $ErrorActionPreference = 'Continue'
    & $ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i $list -c copy $out 2>&1 | ForEach-Object { "$_" } | Out-Host
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    Remove-Item $list -Force -ErrorAction SilentlyContinue
    if ($code -ne 0 -or -not (Test-Path $out)) { throw "Joining the shots failed (FFmpeg exit $code)." }
    $shots = Join-Path $Folder "$Name-shots"
    New-Item -ItemType Directory -Force -Path $shots | Out-Null
    $files | ForEach-Object { Move-Item $_ $shots -Force }
    return $out
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
