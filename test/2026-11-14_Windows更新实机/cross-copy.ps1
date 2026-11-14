param([Parameter(Mandatory=$true)][string]$ExpectedSid)
$ErrorActionPreference = 'Stop'
$qaIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($qaIdentity.User.Value -ne $ExpectedSid -or $ExpectedSid -ne 'S-1-5-21-1133143854-1543063607-2493749742-1010') { throw 'Wrong test account.' }
$qaRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$qaOutput = Join-Path $qaRepo 'output\windows-update-real-20261114'
$qaGatewayPath = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.astrion\quick-entry-gateway.json'
$qaGateway = Get-Content -LiteralPath $qaGatewayPath -Raw | ConvertFrom-Json
$qaExisting = Get-Process -Id $qaGateway.pid
$qaInstalledPrefix = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Astrion') + '\'
if (-not $qaExisting.Path.StartsWith($qaInstalledPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected existing test installation.' }
$qaExistingVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($qaExisting.Path).ProductVersion
if ($qaExistingVersion -notmatch '^0\.10\.1(?:\.|$)') { throw 'The Electron update must be accepted before this check.' }
$qaOther = Join-Path $qaRepo 'output\windows-electron\build\win-unpacked\astrion-desktop.exe'
if ([Diagnostics.FileVersionInfo]::GetVersionInfo($qaOther).ProductVersion -notmatch '^0\.10\.0(?:\.|$)') { throw 'Unexpected portable source version.' }
$qaProcessInfo = New-Object Diagnostics.ProcessStartInfo
$qaProcessInfo.FileName = $qaOther
$qaProcessInfo.WorkingDirectory = Split-Path -Parent $qaOther
$qaProcessInfo.UseShellExecute = $false
$qaAttempted = [Diagnostics.Process]::Start($qaProcessInfo)
$qaHandle = $qaAttempted.Handle
if (-not $qaAttempted.WaitForExit(15000)) { throw 'Second copy remained running; do not assume single-instance handoff.' }
$qaExistingAfter = Get-Process -Id $qaGateway.pid
$qaToken = [IO.File]::ReadAllText((Join-Path $qaGateway.dataRoot 'host\data\host_api_token')).Trim()
$qaResponse = Invoke-RestMethod -Uri ('http://127.0.0.1:' + $qaGateway.port + '/api/host/workspaces') -Headers @{ Authorization = ('Bearer ' + $qaToken) } -TimeoutSec 10
$qaToken = $null
$qaResult = @{ time = (Get-Date).ToString('o'); sameTestUser = $true; sid = $qaIdentity.User.Value; attemptedPath = $qaOther; attemptedVersion = '0.10.0'; attemptedPid = $qaAttempted.Id; exitCode = $qaAttempted.ExitCode; existingPath = $qaExistingAfter.Path; existingVersion = $qaExistingVersion; existingPid = $qaExistingAfter.Id; existingStartUnchanged = ($qaExistingAfter.StartTime -eq $qaExisting.StartTime); existingBackendHealthy = $qaResponse.success; conclusion = 'The second installation exits and hands activation to the already-running copy.' }
if ($qaAttempted.ExitCode -ne 0 -or -not $qaResult.existingStartUnchanged -or -not $qaResult.existingBackendHealthy) { throw 'Cross-copy handoff verification failed.' }
$qaResult | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $qaOutput 'cross-copy-result.json') -Encoding UTF8
$qaResult | ConvertTo-Json -Depth 5
