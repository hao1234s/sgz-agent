param(
    [ValidateSet('start', 'doctor', 'serve', 'capture', 'prepare-client')][string]$Mode = 'start',
    [string]$DshExecutable = 'D:\deepseek\DeepSeek Harness.exe',
    [switch]$Activate,
    [ValidateSet('','chat','knowledge','local')][string]$Profile = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$installedPath = Join-Path $projectRoot '.state\installed-agent-profile.json'
$installedProfile = 'local'
$clientAutoStart = $true
if (Test-Path -LiteralPath $installedPath) {
    $installed = Get-Content -Raw -LiteralPath $installedPath | ConvertFrom-Json
    $installedProfile = $installed.profile
    if ($null -ne $installed.autoStart) { $clientAutoStart = [bool]$installed.autoStart }
}
if (-not $Profile) { $Profile = $installedProfile }
if ($Profile -ne $installedProfile) { throw "Installed profile is $installedProfile. Install the requested $Profile profile before starting DSH." }
if ($Mode -ne 'start' -and $Profile -ne 'local') { throw "Client mode $Mode is disabled in profile $Profile." }
if ($Profile -eq 'local' -and ($Mode -ne 'start' -or $clientAutoStart) -and -not (Test-Path -LiteralPath $python)) { throw 'Run backend\scripts\Sync-Backend.ps1 -Profile local first.' }
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
        if ($Profile -ne 'local' -or -not $clientAutoStart) {
            Write-Output "DSH $Profile started. Python workers start only when their enabled tools are called."
            return
        }
        $deadline = (Get-Date).AddSeconds(20)
        do {
            $workers = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
                $_.ExecutablePath -eq $python -and $_.CommandLine -match '-m\s+sgz_agent(?:\.client\.cli)?\s+serve'
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
    $gameConfig = Join-Path $projectRoot 'backend\config\game.local.json'
    if (-not (Test-Path -LiteralPath $gameConfig)) { $gameConfig = Join-Path $projectRoot 'backend\config\client\game.local.json' }
    $cliArgs = @('-m', 'sgz_agent.client.cli', $Mode, '--config', $gameConfig)
    if ($Activate) { $cliArgs += '--activate' }
    & $python @cliArgs
    if ($LASTEXITCODE -ne 0) { throw "Agent exited with code $LASTEXITCODE" }
} finally { Pop-Location }
