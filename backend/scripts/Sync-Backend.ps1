param([ValidateSet('chat','knowledge','local')][string]$Profile = 'local', [switch]$Dev)
$ErrorActionPreference = 'Stop'
$backendRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $backendRoot
$uv = Join-Path $projectRoot '.tools\uv\uv.exe'
if (-not (Test-Path -LiteralPath $uv)) {
    $uv = (Get-Command uv -ErrorAction Stop).Source
}
$previousEnvironment = $env:UV_PROJECT_ENVIRONMENT
try {
    # The environment stays at the repository root so installed DSH profiles
    # can keep their existing Python executable path after source relocation.
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $projectRoot '.venv'
    $syncArgs = @('sync','--project',$backendRoot,'--frozen')
    if ($Dev) { $syncArgs += @('--extra','dev') }
    if ($Profile -eq 'knowledge') { $syncArgs += @('--extra','knowledge') }
    if ($Profile -eq 'local') { $syncArgs += @('--extra','desktop','--extra','ocr','--extra','api','--extra','knowledge') }
    & $uv @syncArgs
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency sync failed with code $LASTEXITCODE" }
} finally {
    $env:UV_PROJECT_ENVIRONMENT = $previousEnvironment
}
