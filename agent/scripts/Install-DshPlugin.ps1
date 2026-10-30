param([string]$DshExecutable = 'D:\deepseek\DeepSeek Harness.exe',
    [ValidateSet('chat','knowledge','local')][string]$Profile = 'local', [switch]$NoAutoStart)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue) {
    throw 'Fully quit DeepSeek Harness, including its tray process, before installing.'
}
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$profileDirectory = Join-Path $env:USERPROFILE '.dsh\profiles\desktop'
if (-not (Test-Path -LiteralPath (Join-Path $profileDirectory 'package.json'))) { throw 'DSH desktop profile has not been initialized.' }
$pluginVersion = (Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'agent\plugin\package.json') | ConvertFrom-Json).version
$pluginRoot = Join-Path $projectRoot 'agent\plugin'
if (-not (Test-Path -LiteralPath (Join-Path $pluginRoot 'dist\index.js'))) { throw 'Build the plugin first.' }
$archiveDirectory = Join-Path $projectRoot ('.state\package\' + $Profile + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$staging = Join-Path $archiveDirectory 'package'
New-Item -ItemType Directory -Force -Path $staging | Out-Null
Copy-Item -LiteralPath (Join-Path $pluginRoot 'package.json') -Destination $staging
Copy-Item -LiteralPath (Join-Path $pluginRoot 'dist') -Destination $staging -Recurse
$renderArgs = @((Join-Path $PSScriptRoot 'Render-Profile.mjs'),'--profile',$Profile,'--root',$projectRoot,'--output',(Join-Path $staging 'cordis.patch.yml'))
if ($NoAutoStart) { $renderArgs += '--no-auto-start' }
& node @renderArgs
if ($LASTEXITCODE -ne 0) { throw 'Profile rendering failed.' }
Push-Location $staging
try {
    & npm.cmd pack --ignore-scripts --pack-destination $archiveDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Profile package build failed.' }
} finally { Pop-Location }
$archive = Join-Path $archiveDirectory "dsh-sgz-agent-$pluginVersion.tgz"
$backup = Join-Path $projectRoot ('.state\dsh-profile-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($name in @('package.json','pnpm-lock.yaml','pnpm-workspace.yaml','cordis.yml','cordis.yaml','cordis.patch.yml','compatibility.json','config.yml','config.yaml')) {
    $source = Join-Path $profileDirectory $name
    if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $backup $name) }
}
$installedMarker = Join-Path $projectRoot '.state\installed-agent-profile.json'
if (Test-Path -LiteralPath $installedMarker) { Copy-Item -LiteralPath $installedMarker -Destination (Join-Path $backup 'installed-agent-profile.json') }
$carrier = Join-Path (Split-Path -Parent $DshExecutable) 'resources\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js'
$previousElectronNode = $env:ELECTRON_RUN_AS_NODE
try {
    $env:ELECTRON_RUN_AS_NODE = '1'
    $cliArgs = @('--expose-internals', ('"' + $carrier + '"'), 'plugin', '--profile', 'desktop', 'add', ('"' + $archive + '"'), '--ignore-scripts')
    $process = Start-Process -FilePath $DshExecutable -ArgumentList $cliArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $backup 'install.out') -RedirectStandardError (Join-Path $backup 'install.err')
    Get-Content -LiteralPath (Join-Path $backup 'install.out'), (Join-Path $backup 'install.err')
    if ($process.ExitCode -ne 0) { throw "DSH installation failed; backup and diagnostics: $backup" }
    @{profile=$Profile; rootDir=$projectRoot; archive=$archive; backup=$backup; autoStart=(-not $NoAutoStart)} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $projectRoot '.state\installed-agent-profile.json')
    Write-Output "DSH $Profile plugin installed; original profile metadata backed up at $backup"
} finally { $env:ELECTRON_RUN_AS_NODE = $previousElectronNode }
