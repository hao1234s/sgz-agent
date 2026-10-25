param(
    [ValidateSet('start', 'doctor', 'serve', 'capture', 'prepare-client')][string]$Mode = 'start',
    [string]$DshExecutable = 'D:\deepseek\DeepSeek Harness.exe',
    [switch]$Activate
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run uv sync --frozen --extra dev --extra ocr first.' }
$env:PYTHONIOENCODING = 'utf-8'
Push-Location $projectRoot
try {
    if ($Mode -eq 'start') {
        if (-not (Test-Path -LiteralPath $DshExecutable)) { throw "DSH executable not found: $DshExecutable" }
        $dsh = Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue
        if (-not $dsh) {
            # Editor/tool shells may inherit Electron's Node-only mode.
            $previousElectronNode = $env:ELECTRON_RUN_AS_NODE
            try {
                $env:ELECTRON_RUN_AS_NODE = $null
                Start-Process -FilePath $DshExecutable -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
            } finally { $env:ELECTRON_RUN_AS_NODE = $previousElectronNode }
        }
        $deadline = (Get-Date).AddSeconds(20)
        do {
            $workers = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
                $_.ExecutablePath -eq $python -and $_.CommandLine -match '-m\s+sgz_agent\s+serve'
            })
            if ($workers.Count -gt 0) { break }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $deadline)
        if ($workers.Count -ne 1) {
            throw "Expected one plugin-managed sgz agent worker, found $($workers.Count). Check DSH plugin startup and sgz_game_status; do not start another serve process."
        }
        Write-Output "DSH-managed agent worker running (PID $($workers[0].ProcessId)). Use sgz_game_status to check world, calibration and task status."
        return
    }
    $cliArgs = @('-m', 'sgz_agent', $Mode, '--config', 'config/game.local.json')
    if ($Activate) { $cliArgs += '--activate' }
    & $python @cliArgs
    if ($LASTEXITCODE -ne 0) { throw "Agent exited with code $LASTEXITCODE" }
} finally { Pop-Location }
