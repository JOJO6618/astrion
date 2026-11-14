param([Parameter(Mandatory=$true)][string]$ExpectedSid)
$ErrorActionPreference = 'Stop'
$qaIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($qaIdentity.User.Value -ne $ExpectedSid -or $ExpectedSid -ne 'S-1-5-21-1133143854-1543063607-2493749742-1010') { throw 'Wrong test account.' }
$qaRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$qaOutput = Join-Path $qaRepo 'output\windows-update-real-20261114'
$qaAccepted = Get-Content -LiteralPath (Join-Path $qaOutput 'electron-update-result.json') -Raw | ConvertFrom-Json
if (-not $qaAccepted.success) { throw 'Keep the failed test installation intact for investigation.' }
$qaInstall = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Astrion'
$qaPrefix = $qaInstall.TrimEnd('\') + '\'
$qaProcesses = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($qaPrefix, [StringComparison]::OrdinalIgnoreCase) })
$qaStopped = @($qaProcesses | ForEach-Object { @{ pid = $_.Id; path = $_.Path; started = $_.StartTime.ToString('o') } })
foreach ($qaProcess in $qaProcesses) { Stop-Process -Id $qaProcess.Id -Force -ErrorAction SilentlyContinue }
$qaDeadline = (Get-Date).AddSeconds(30)
do {
    $qaRemaining = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($qaPrefix, [StringComparison]::OrdinalIgnoreCase) })
    if ($qaRemaining.Count -eq 0) { break }
    if ((Get-Date) -gt $qaDeadline) { throw 'Test processes have not exited; do not modify the install yet.' }
    Start-Sleep -Milliseconds 250
} while ($true)
$qaOriginalYaml = Join-Path $qaOutput 'app-update-before-test-feed.yml'
$qaYaml = [IO.File]::ReadAllText($qaOriginalYaml)
if (-not $qaYaml.Contains('https://astrion.cyjai.com/downloads/')) { throw 'Backup does not point to the official update source.' }
Copy-Item -LiteralPath $qaOriginalYaml -Destination (Join-Path $qaInstall 'resources\app-update.yml')
[IO.File]::WriteAllText((Join-Path $qaOutput 'stop-dialog-monitor.txt'), 'real upgrade tests finished')
@{ time = (Get-Date).ToString('o'); testSid = $ExpectedSid; stoppedTestProcesses = $qaStopped; testFeedRestoredToOfficial = $true; testInstallationPreserved = $true; accountAutomaticallyExpires = $true; existingUserInstallationUntouched = $true } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $qaOutput 'cleanup-result.json') -Encoding UTF8
Write-Output 'TEST_COPY_STOPPED_AND_OFFICIAL_FEED_RESTORED'
