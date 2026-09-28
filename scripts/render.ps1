<#
.SYNOPSIS
  Render one or more CS Demo Manager video configs (built by Claude) with HLAE + FFmpeg.

.EXAMPLE
  git pull
  .\scripts\render.ps1 -Config videos\configs\mirage-ace.csdm.json
  .\scripts\render.ps1 -All            # every config in videos\configs
  .\scripts\render.ps1 -Config ... -OutputFolder "D:\Renders"

  Steam must be running and CS2 must be CLOSED before starting. Don't touch the CS2 window while it records.
  After rendering, run .\scripts\safety-check.ps1 before playing online.
#>
param(
    [string[]]$Config,
    [switch]$All,
    [string]$OutputFolder
)
. (Join-Path $PSScriptRoot '_common.ps1')

if ($All) { $Config = @(Get-ChildItem (Join-Path $RepoRoot 'videos\configs') -Filter *.csdm.json | ForEach-Object FullName) }
if (-not $Config -or $Config.Count -eq 0) { throw "Pass -Config <file> or -All." }
if (-not (Get-Process steam -ErrorAction SilentlyContinue)) { throw "Steam is not running. Start Steam first." }
# SAFETY: never attach a recording session to a CS2 that may be a normal (VAC-secured) game. Close it first.
if (Get-Process cs2 -ErrorAction SilentlyContinue) {
    throw "CS2 is already running. Close it completely (it may be a normal online session), then run this again."
}

$csdm = Get-Csdm
$logDir = Join-Path $RepoRoot 'videos\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

foreach ($file in $Config) {
    $file = (Resolve-Path $file).Path
    $cfg = Get-Content $file -Raw | ConvertFrom-Json
    if (-not (Test-Path $cfg.demoPath)) { throw "Demo not found: $($cfg.demoPath) (referenced by $file)" }

    $code = Invoke-Csdv validate $file
    if ($code -ne 0) { throw "Config failed validation: $file" }

    # Camera paths live in this repo: replace the {REPO} placeholder with this clone's path (forward slashes,
    # which HLAE accepts and which need no JSON escaping).
    $repoForward = ($RepoRoot -replace '\\', '/')
    if ($repoForward.Contains('//') -or $repoForward.Contains('#')) {
        throw "Repo path '$repoForward' contains '//' or '#', which CS:DM strips from configs. Clone it to a plain local folder."
    }
    $raw = Get-Content $file -Raw
    foreach ($m in [regex]::Matches($raw, '\{REPO\}/([^"\\]+\.xml)')) {
        $xml = Join-Path $RepoRoot $m.Groups[1].Value
        if (-not (Test-Path $xml)) { throw "Camera path file missing: $xml (git pull?)" }
    }
    $raw = $raw.Replace('{REPO}', $repoForward)
    # CS:DM ignores --output when a config file is used (2026-09-27: the files landed next to the demo), so the
    # folder goes into the config's outputFolderPath instead.
    $folder = if ($OutputFolder) { $OutputFolder } else { $cfg.outputFolderPath }
    if ($folder) {
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        $folderJson = (Resolve-Path $folder).Path | ConvertTo-Json
        if ($raw -match '"outputFolderPath"\s*:') {
            $raw = [regex]::Replace($raw, '"outputFolderPath"\s*:\s*"(?:[^"\\]|\\.)*"', { '"outputFolderPath": ' + $folderJson })
        } else {
            $raw = ([regex]'\{').Replace($raw, { '{' + "`n  " + '"outputFolderPath": ' + $folderJson + ',' }, 1)
        }
    }
    # CS:DM's own concatenation writes an FFmpeg list with unescaped quotes, so any path with an apostrophe
    # (C:\Users\Liam's PC\...) fails ("Impossible to open 'C:\Users\Liams'", 2026-09-27). Join the clips ourselves.
    $joinAfter = [bool]$cfg.concatenateSequences
    if ($joinAfter) { $raw = [regex]::Replace($raw, '"concatenateSequences"\s*:\s*true', '"concatenateSequences": false') }
    $runFile = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetFileName($file))
    [IO.File]::WriteAllText($runFile, $raw, (New-Object System.Text.UTF8Encoding $false))

    # ReShade (loaded by HLAE via CS:DM's HLAE parameters): pick this render's preset, "off" unless the spec asked.
    $reshadePreset = 'clean'   # deband only: smooth dark gradients in every render (banding fix)
    $sidecar = $file -replace '\.csdm\.json$', '.reshade'   # written by csdv build (spec "reshade": "<preset>")
    if (Test-Path $sidecar) { $reshadePreset = (Get-Content $sidecar -Raw).Trim() }
    Set-ReShadePreset $reshadePreset

    $cliArgs = @('video', '--config-file', $runFile)
    $log = Join-Path $logDir (([IO.Path]::GetFileNameWithoutExtension($file)) + '.log')
    Write-Host "Rendering $file ($($cfg.sequences.Count) sequences, $($cfg.width)x$($cfg.height)@$($cfg.framerate))..." -ForegroundColor Cyan
    # Windows PowerShell turns redirected stderr into errors; don't let that abort the render.
    $ErrorActionPreference = 'Continue'
    & $csdm @cliArgs --verbose 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    Set-ReShadePreset 'clean'
    if ($exitCode -eq 0 -and (Select-String -Path $log -Pattern 'FFmpeg error' -Quiet)) { $exitCode = 3 }  # csdm exits 0 anyway
    if ($exitCode -ne 0) {
        Write-Host "FAILED - log: $log (commit it so Claude can debug)" -ForegroundColor Red
        exit $exitCode
    }
    Start-Sleep -Seconds 3
    if (Get-Process cs2, hlae -ErrorAction SilentlyContinue) {
        Write-Host "CS2/HLAE from the recording is still open. Close it before playing online (see scripts\safety-check.ps1)." -ForegroundColor Yellow
    }
    $dest = if ($OutputFolder) { $OutputFolder } elseif ($cfg.outputFolderPath) { $cfg.outputFolderPath } else { Split-Path $cfg.demoPath }
    if ($joinAfter) {
        $name = if ($cfg.outputFileName -and -not $cfg.outputFileName.Contains('{')) { $cfg.outputFileName } else {
            [IO.Path]::GetFileNameWithoutExtension([IO.Path]::GetFileNameWithoutExtension($file)) }
        $joined = Join-Sequences -Folder $dest -Name $name -Config $cfg
        Write-Host "Joined $($cfg.sequences.Count) shots -> $joined (separate shots in $name-shots\)" -ForegroundColor Green
    }
    Write-Host "Done. Output is in: $dest" -ForegroundColor Green
}
