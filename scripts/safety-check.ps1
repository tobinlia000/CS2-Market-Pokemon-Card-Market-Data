<#
.SYNOPSIS
  Run BEFORE playing CS2 online (matchmaking, Premier, FACEIT, community servers) after a recording session.

  HLAE is a cheat as far as VAC is concerned. CS:DM and HLAE always start CS2 with -insecure (VAC off, and
  VAC-secured servers refuse the connection), and both refuse to run without it. This script makes sure nothing
  from a recording session is still running or installed, so your next normal launch from Steam is clean.

.EXAMPLE
  .\scripts\safety-check.ps1          # report only
  .\scripts\safety-check.ps1 -Fix     # also close CS2/HLAE and restore gameinfo.gi / remove the CS:DM plugin
#>
param([switch]$Fix)
. (Join-Path $PSScriptRoot '_common.ps1')

$issues = @(Get-RecordingLeftovers)
if ($issues.Count -eq 0) {
    Write-Host "SAFE: no CS2/HLAE process running and no CS:DM plugin installed." -ForegroundColor Green
    Write-Host "Start CS2 normally from Steam (never from CS Demo Manager or HLAE) to play online."
    exit 0
}

Write-Host "NOT SAFE TO PLAY ONLINE YET:" -ForegroundColor Red
$issues | ForEach-Object { Write-Host "  - $_" }
if (-not $Fix) {
    Write-Host "`nClose CS Demo Manager's game, or run: .\scripts\safety-check.ps1 -Fix" -ForegroundColor Yellow
    exit 1
}

Get-Process cs2, hlae -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
foreach ($csgo in Get-Cs2GameFolders) {
    $gameinfo = Join-Path $csgo 'gameinfo.gi'
    if (Test-Path "$gameinfo.backup") {
        Copy-Item "$gameinfo.backup" $gameinfo -Force
        Remove-Item "$gameinfo.backup" -Force
    } elseif ((Test-Path $gameinfo) -and ((Get-Content $gameinfo -Raw) -match 'csgo/csdm')) {
        $content = (Get-Content $gameinfo -Raw) -replace "Game\tcsgo/csdm\r?\n\s*", ''
        Set-Content -Path $gameinfo -Value $content -NoNewline
    }
    Remove-Item (Join-Path $csgo 'csdm') -Recurse -Force -ErrorAction SilentlyContinue
}

$remaining = @(Get-RecordingLeftovers)
if ($remaining.Count -eq 0) {
    Write-Host "Fixed. SAFE: start CS2 from Steam to play online." -ForegroundColor Green
    Write-Host "If anything looks off, Steam > CS2 > Properties > Installed Files > Verify integrity."
    exit 0
}
Write-Host "Still not clean:" -ForegroundColor Red
$remaining | ForEach-Object { Write-Host "  - $_" }
Write-Host "Use Steam > CS2 > Properties > Installed Files > Verify integrity of game files."
exit 1
