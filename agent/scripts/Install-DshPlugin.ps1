param([string]$DshExecutable = 'D:\deepseek\DeepSeek Harness.exe')
$ErrorActionPreference = 'Stop'
if (Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue) {
    throw 'Fully quit DeepSeek Harness, including its tray process, before installing.'
}
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$profileDirectory = Join-Path $env:USERPROFILE '.dsh\profiles\desktop'
if (-not (Test-Path -LiteralPath (Join-Path $profileDirectory 'package.json'))) { throw 'DSH desktop profile has not been initialized.' }
$pluginVersion = (Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'agent\plugin\package.json') | ConvertFrom-Json).version
$archive = Join-Path $projectRoot ".state\package\dsh-sgz-agent-$pluginVersion.tgz"
if (-not (Test-Path -LiteralPath $archive)) { throw 'Build and npm pack the plugin first.' }
$backup = Join-Path $projectRoot ('.state\dsh-profile-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($name in @('package.json','pnpm-lock.yaml','pnpm-workspace.yaml')) {
    $source = Join-Path $profileDirectory $name
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $backup $name) }
}
$carrier = Join-Path (Split-Path -Parent $DshExecutable) 'resources\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js'
$previousElectronNode = $env:ELECTRON_RUN_AS_NODE
try {
    $env:ELECTRON_RUN_AS_NODE = '1'
    $cliArgs = @('--expose-internals', ('"' + $carrier + '"'), 'plugin', '--profile', 'desktop', 'add', ('"' + $archive + '"'), '--ignore-scripts')
    $process = Start-Process -FilePath $DshExecutable -ArgumentList $cliArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $backup 'install.out') -RedirectStandardError (Join-Path $backup 'install.err')
    Get-Content -LiteralPath (Join-Path $backup 'install.out'), (Join-Path $backup 'install.err')
    if ($process.ExitCode -ne 0) { throw "DSH installation failed; backup and diagnostics: $backup" }
    Write-Output "DSH plugin installed; original package metadata backed up at $backup"
} finally { $env:ELECTRON_RUN_AS_NODE = $previousElectronNode }
