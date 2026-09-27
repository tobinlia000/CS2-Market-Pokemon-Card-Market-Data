<#
.SYNOPSIS
  Render one or more CS Demo Manager video configs (built by Claude) with HLAE + FFmpeg.

.EXAMPLE
  git pull
  .\scripts\render.ps1 -Config videos\configs\mirage-ace.csdm.json
  .\scripts\render.ps1 -All            # every config in videos\configs
  .\scripts\render.ps1 -Config ... -OutputFolder "D:\Renders"

  Steam must be running. Don't touch the CS2 window while it records.
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

$csdm = Get-Csdm
$logDir = Join-Path $RepoRoot 'videos\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

foreach ($file in $Config) {
    $file = (Resolve-Path $file).Path
    $cfg = Get-Content $file -Raw | ConvertFrom-Json
    if (-not (Test-Path $cfg.demoPath)) { throw "Demo not found: $($cfg.demoPath) (referenced by $file)" }

    $code = Invoke-Csdv validate $file
    if ($code -ne 0) { throw "Config failed validation: $file" }

    $cliArgs = @('video', '--config-file', $file)
    if ($OutputFolder) { $cliArgs += @('--output', (Resolve-Path $OutputFolder).Path) }
    $log = Join-Path $logDir (([IO.Path]::GetFileNameWithoutExtension($file)) + '.log')
    Write-Host "Rendering $file ($($cfg.sequences.Count) sequences, $($cfg.width)x$($cfg.height)@$($cfg.framerate))..." -ForegroundColor Cyan
    # Windows PowerShell turns redirected stderr into errors; don't let that abort the render.
    $ErrorActionPreference = 'Continue'
    & $csdm @cliArgs --verbose 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($exitCode -ne 0) {
        Write-Host "FAILED - log: $log (commit it so Claude can debug)" -ForegroundColor Red
        exit $exitCode
    }
    $dest = if ($OutputFolder) { $OutputFolder } elseif ($cfg.outputFolderPath) { $cfg.outputFolderPath } else { Split-Path $cfg.demoPath }
    Write-Host "Done. Output is in a sub-folder (named after the video id) of: $dest" -ForegroundColor Green
}
