param([string]$DshExecutable, [switch]$Installed,
    [ValidateSet('chat','knowledge','local')][string]$Profile = 'local', [string]$PythonPath)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:PYTHONIOENCODING = 'utf-8'
if (-not $PythonPath) {
    $PythonPath = Join-Path $projectRoot $(if ($env:OS -eq 'Windows_NT') { '.venv\Scripts\python.exe' } else { '.venv/bin/python' })
}
$testArgs = @('-m','pytest','-q')
if ($Profile -ne 'local') {
    $testArgs += @('tests/test_analysis_cli.py','tests/test_combat_assessment.py','tests/test_rpc.py','tests/test_dependency_contract.py',
        'tests/test_module_boundaries.py::test_architecture_checker_detects_lazy_and_legacy_reverse_imports',
        'tests/test_module_boundaries.py::test_capability_imports_follow_boundaries')
    if ($Profile -eq 'knowledge') {
        $testArgs += @('tests/test_knowledge.py','tests/test_knowledge_cli.py','tests/test_knowledge_sources.py','tests/test_knowledge_curated.py',
            'tests/test_module_boundaries.py::test_knowledge_rpc_blocks_all_client_and_desktop_imports')
    }
}
$npm = if ($env:OS -eq 'Windows_NT') { 'npm.cmd' } else { 'npm' }

Push-Location (Join-Path $projectRoot 'backend')
try {
    & $PythonPath @testArgs
    if ($LASTEXITCODE -ne 0) { throw 'Python tests failed' }
    Push-Location (Join-Path $projectRoot 'agent\plugin')
    try {
        & $npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Plugin build failed' }
        & $npm test
        if ($LASTEXITCODE -ne 0) { throw 'Plugin tests failed' }
    } finally { Pop-Location }
    if ($DshExecutable -and $Profile -ne 'local') { throw 'Use Test-Profiles.mjs for the isolated chat/knowledge DSH smoke test.' }
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
