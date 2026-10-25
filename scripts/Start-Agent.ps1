param(
    [ValidateSet('doctor', 'serve', 'capture')][string]$Mode = 'doctor',
    [switch]$Activate
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run uv sync --frozen --extra dev --extra ocr first.' }
$env:PYTHONIOENCODING = 'utf-8'
Push-Location $projectRoot
try {
    $cliArgs = @('-m', 'sgz_agent', $Mode, '--config', 'config/game.local.json')
    if ($Activate) { $cliArgs += '--activate' }
    & $python @cliArgs
    if ($LASTEXITCODE -ne 0) { throw "Agent exited with code $LASTEXITCODE" }
} finally { Pop-Location }
