<#
.SYNOPSIS
  Analyze a demo with CS Demo Manager and write a compact summary Claude can read.

.EXAMPLE
  .\scripts\export-demo.ps1 -Demo "C:\Users\me\Videos\demos\match.dem"
  .\scripts\export-demo.ps1 -Demo "D:\demos\faceit_123.dem" -Name mirage-faceit
  .\scripts\export-demo.ps1 -Demo "C:\...\game\csgo\mango1.dem" -Source valve   # self-recorded demo, "?" source in CS:DM

  Writes videos\demos\<Name>.summary.json. Commit and push it so Claude can plan clips.
#>
param(
    [Parameter(Mandatory = $true)][string]$Demo,
    [string]$Name,
    # Analyzer logic for demos CS:DM can't identify (UnknownSource), e.g. valve, faceit, esl, matchzy.
    [string]$Source
)
. (Join-Path $PSScriptRoot '_common.ps1')

$Demo = (Resolve-Path $Demo).Path
if (-not $Demo.ToLower().EndsWith('.dem')) { throw "Not a .dem file: $Demo" }
if (-not $Name) { $Name = [IO.Path]::GetFileNameWithoutExtension($Demo) }
$Name = $Name -replace '[^A-Za-z0-9._-]', '-'

$csdm = Get-Csdm
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("csdm-export-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Write-Host "Analyzing + exporting $Demo (first analysis can take a minute)..."
    $sourceArgs = @(); if ($Source) { $sourceArgs = @('--source', $Source) }
    & $csdm json $Demo --output-folder $tmp --minify @sourceArgs
    if ($LASTEXITCODE -ne 0) { throw "csdm json failed (exit $LASTEXITCODE)" }
    $export = Get-ChildItem $tmp -Filter *.json | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $export) { throw "csdm json produced no file in $tmp" }

    $out = Join-Path $RepoRoot "videos\demos\$Name.summary.json"
    $code = Invoke-Csdv summarize $export.FullName -o $out
    if ($code -ne 0) { throw "csdv summarize failed (exit $code)" }
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Next: share it with Claude:" -ForegroundColor Green
Write-Host "  git add videos/demos/$Name.summary.json; git commit -m 'Add $Name demo summary'; git push"
