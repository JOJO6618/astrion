param([Parameter(Mandatory=$true)][string]$ExpectedSid)
$ErrorActionPreference = 'Stop'
$qaIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($qaIdentity.User.Value -ne $ExpectedSid -or $ExpectedSid -ne 'S-1-5-21-1133143854-1543063607-2493749742-1010') { throw 'Wrong test account.' }
$qaRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$qaOutput = Join-Path $qaRepo 'output\windows-update-real-20261114'
$qaInstall = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Astrion'
$qaExecutable = Join-Path $qaInstall 'astrion-desktop.exe'
if ([Diagnostics.FileVersionInfo]::GetVersionInfo($qaExecutable).ProductVersion -notmatch '^0\.10\.0(?:\.|$)') { throw 'Expected the 0.10.0 produced by the real migration.' }
$qaFirst = Get-Content -LiteralPath (Join-Path $qaOutput 'migration-observation.json') -Raw | ConvertFrom-Json
if ($qaFirst.executableVersion -notmatch '^0\.10\.0(?:\.|$)' -or -not $qaFirst.health.success -or @($qaFirst.payload.missing).Count -ne 0 -or @($qaFirst.payload.wrongSize).Count -ne 0) { throw 'First real migration evidence is not accepted.' }
Copy-Item -LiteralPath (Join-Path $qaOutput 'migration-observation.json') -Destination (Join-Path $qaOutput 'migration-first-result.json')
$qaPrefix = $qaInstall.TrimEnd('\') + '\'
$qaOwnProcesses = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($qaPrefix, [StringComparison]::OrdinalIgnoreCase) })
$qaStopped = @($qaOwnProcesses | ForEach-Object { @{ pid = $_.Id; path = $_.Path; started = $_.StartTime.ToString('o') } })
# This account has no user work. Stop only its already-accepted test copy to
# configure the unpublished next-version feed. Never match a process by name.
foreach ($qaProcess in $qaOwnProcesses) { Stop-Process -Id $qaProcess.Id -Force -ErrorAction SilentlyContinue }
$qaDeadline = (Get-Date).AddSeconds(30)
do {
    $qaRemaining = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($qaPrefix, [StringComparison]::OrdinalIgnoreCase) })
    if ($qaRemaining.Count -eq 0) { break }
    if ((Get-Date) -gt $qaDeadline) { throw 'Test processes have not released their files; refusing to prepare another run.' }
    Start-Sleep -Milliseconds 250
} while ($true)
$qaBackendFile = Join-Path $qaInstall 'resources\runtime\backend\server\status\desktop_update.py'
$qaYamlFile = Join-Path $qaInstall 'resources\app-update.yml'
$qaBackend = [IO.File]::ReadAllText($qaBackendFile)
$qaOldBase = '_UPDATE_MANIFEST_BASE = "https://astrion.cyjai.com/downloads"'
if (-not $qaBackend.Contains($qaOldBase)) { throw 'Unexpected test backend update source; refusing a broad rewrite.' }
Copy-Item -LiteralPath $qaBackendFile -Destination (Join-Path $qaOutput 'desktop_update-before-test-feed.py')
Copy-Item -LiteralPath $qaYamlFile -Destination (Join-Path $qaOutput 'app-update-before-test-feed.yml')
$qaEncoding = New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText($qaBackendFile, $qaBackend.Replace($qaOldBase, '_UPDATE_MANIFEST_BASE = "http://127.0.0.1:19976"'), $qaEncoding)
$qaYaml = [IO.File]::ReadAllText($qaYamlFile)
if (-not ($qaYaml -match '(?m)^url:')) { throw 'Missing real updater YAML URL.' }
[IO.File]::WriteAllText($qaYamlFile, ([regex]::Replace($qaYaml, '(?m)^url:[^\r\n]*', 'url: http://127.0.0.1:19976/')), $qaEncoding)
$qaDataRoot = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.astrion\astrion-desktop'
$qaSentinelPath = Join-Path $qaDataRoot 'host\data\qa-preserve-before-electron-update.txt'
$qaSentinel = 'real-electron-update-' + [Guid]::NewGuid().ToString('N')
[IO.File]::WriteAllText($qaSentinelPath, $qaSentinel, $qaEncoding)
$qaApp = Start-Process -FilePath $qaExecutable -WorkingDirectory $qaInstall -ArgumentList '--remote-debugging-address=127.0.0.1 --remote-debugging-port=19974' -PassThru
@{ time = (Get-Date).ToString('o'); pid = $qaApp.Id; executable = $qaExecutable; currentVersion = '0.10.0'; targetVersion = '0.10.1'; cdp = 'http://127.0.0.1:19974'; feed = 'http://127.0.0.1:19976/'; testFeedOnly = $true; sid = $ExpectedSid; stoppedTestProcesses = $qaStopped; sentinelPath = $qaSentinelPath; sentinel = $qaSentinel; backendTestFeedSha256 = (Get-FileHash -LiteralPath $qaBackendFile -Algorithm SHA256).Hash; updaterYamlTestFeedSha256 = (Get-FileHash -LiteralPath $qaYamlFile -Algorithm SHA256).Hash } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $qaOutput 'electron-launch.json') -Encoding UTF8
Write-Output ('REAL_ELECTRON_TEST_APP_PID ' + $qaApp.Id)
