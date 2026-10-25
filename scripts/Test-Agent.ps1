param([string]$DshExecutable, [switch]$Installed)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
Push-Location $projectRoot
try {
    & '.\.venv\Scripts\python.exe' -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Python tests failed' }
    Push-Location (Join-Path $projectRoot 'plugin')
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Plugin build failed' }
        & npm.cmd test
        if ($LASTEXITCODE -ne 0) { throw 'Plugin tests failed' }
    } finally { Pop-Location }
    if ($DshExecutable) {
        $logs = Join-Path $projectRoot '.state\logs'
        New-Item -ItemType Directory -Force -Path $logs | Out-Null
        $previousElectronNode = $env:ELECTRON_RUN_AS_NODE
        try {
            $env:ELECTRON_RUN_AS_NODE = '1'
            $scriptPath = Join-Path $PSScriptRoot 'Test-Dsh.cjs'
            $dshArgs = @('--expose-internals', ('"' + $scriptPath + '"'))
            if ($Installed) { $dshArgs += '--installed' }
            $process = Start-Process -FilePath $DshExecutable -ArgumentList $dshArgs -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $logs 'dsh.out') -RedirectStandardError (Join-Path $logs 'dsh.err')
            Get-Content -Encoding UTF8 -LiteralPath (Join-Path $logs 'dsh.out'), (Join-Path $logs 'dsh.err')
            if ($process.ExitCode -ne 0) { throw 'Installed DSH integration check failed' }
        } finally { $env:ELECTRON_RUN_AS_NODE = $previousElectronNode }
    }
} finally { Pop-Location }
