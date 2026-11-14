$ErrorActionPreference = 'Stop'
$qaRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$qaOutput = Join-Path $qaRepo 'output\windows-update-real-20261114'
$qaChecks = @()
foreach ($qaFile in Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' -File) {
    $qaTokens = $null
    $qaErrors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($qaFile.FullName, [ref]$qaTokens, [ref]$qaErrors)
    if (@($qaErrors).Count) { throw ($qaFile.Name + ': ' + ($qaErrors.Message -join '; ')) }
    $qaChecks += $qaFile.Name
}
foreach ($qaFile in Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object { $_.Extension -in @('.mjs', '.cjs') }) {
    & node --check $qaFile.FullName
    if ($LASTEXITCODE -ne 0) { throw ('Node syntax check failed: ' + $qaFile.Name) }
    $qaChecks += $qaFile.Name
}
$qaBaseline = Get-Content -LiteralPath (Join-Path $qaOutput 'protected-processes.json') -Raw | ConvertFrom-Json
foreach ($qaRow in $qaBaseline.processes) {
    $qaProcess = Get-Process -Id $qaRow.pid -ErrorAction Stop
    if ($qaProcess.StartTime.ToUniversalTime().ToString('o') -ne $qaRow.started -or $qaProcess.Path -ne $qaRow.path) { throw ('Protected process identity changed: ' + $qaRow.pid) }
}
$qaFirst = Get-Content -LiteralPath (Join-Path $qaOutput 'migration-first-result.json') -Raw | ConvertFrom-Json
$qaNext = Get-Content -LiteralPath (Join-Path $qaOutput 'electron-update-result.json') -Raw | ConvertFrom-Json
$qaCleanup = Get-Content -LiteralPath (Join-Path $qaOutput 'cleanup-result.json') -Raw | ConvertFrom-Json
if ($qaFirst.executableVersion -ne '0.10.0.0' -or -not $qaFirst.health.success -or @($qaFirst.payload.missing).Count -or @($qaFirst.payload.wrongSize).Count) { throw 'First migration receipt failed.' }
if (-not $qaNext.success -or $qaNext.observation.executableVersion -ne '0.10.1.0' -or -not $qaNext.observation.sentinelPreserved -or -not $qaNext.observation.health.success -or @($qaNext.observation.payload.missing).Count -or @($qaNext.observation.payload.wrongSize).Count -or @($qaNext.observation.criticalHashes | Where-Object { -not $_.matches }).Count) { throw 'Electron upgrade receipt failed.' }
if (-not $qaCleanup.testFeedRestoredToOfficial) { throw 'Cleanup receipt failed.' }
$qaInstall = Split-Path $qaNext.observation.path -Parent
$qaPrefix = $qaInstall.TrimEnd('\') + '\'
$qaRemaining = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($qaPrefix, [StringComparison]::OrdinalIgnoreCase) })
if ($qaRemaining.Count) { throw 'Test installation still has running processes.' }
& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'invoke-isolated.ps1') -Mode VerifySources
if ($LASTEXITCODE -ne 0) { throw 'Isolated source verification failed.' }
$qaSourceReceipt = Get-Content -LiteralPath (Join-Path $qaOutput 'official-sources-verification.json') -Raw | ConvertFrom-Json
if (-not $qaSourceReceipt.success -or -not $qaSourceReceipt.officialUpdateSourcesVerified) { throw 'Official source verification receipt failed.' }
if (@(Get-NetTCPConnection -LocalPort 19976 -State Listen -ErrorAction SilentlyContinue).Count) { throw 'Temporary update source still listening.' }
@{ success = $true; time = (Get-Date).ToString('o'); syntaxChecked = $qaChecks; protectedProcessCount = @($qaBaseline.processes).Count; protectedProcessesUnchanged = $true; testProcessesRemaining = 0; officialUpdateSourcesVerified = $true; temporaryFeedStopped = $true; upgradeReceiptsVerified = $true } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $qaOutput 'final-verification.json') -Encoding UTF8
Write-Output ('FINAL_VERIFICATION_PASSED syntax=' + $qaChecks.Count + ' protected=' + @($qaBaseline.processes).Count)
