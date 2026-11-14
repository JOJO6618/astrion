$ErrorActionPreference = 'Stop'
$qaRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$qaOutput = Join-Path $qaRepo 'output\windows-update-progress-20261114'
$qaBuild = Join-Path $qaOutput 'build'
$qaStage = Join-Path $qaOutput 'release-0.10.0'
$qaUtf8 = New-Object Text.UTF8Encoding $false
# Compilation checks only. Do not launch the progress UI or perform an update.
$qaScripts = @(
    (Join-Path $qaRepo 'desktop-electron\build\astrion-install-guard.ps1'),
    (Join-Path $qaRepo 'desktop-electron\build\astrion-install-progress.ps1'),
    (Join-Path $PSScriptRoot 'prepare-manual-legacy.ps1'),
    $PSCommandPath
)
foreach ($qaScript in $qaScripts) {
    $qaTokens = $null
    $qaErrors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($qaScript, [ref]$qaTokens, [ref]$qaErrors)
    if (@($qaErrors).Count) { throw ($qaScript + ': ' + ($qaErrors.Message -join '; ')) }
}
$qaInstaller = Join-Path $qaBuild 'Astrion-0.10.0-windows-x64-setup.exe'
if (-not [IO.File]::Exists($qaInstaller)) { throw 'Compiled installer missing.' }
$qaKey = Join-Path $env:USERPROFILE '.astrion-desktop-keys\updater.key'
$qaDesktop = Join-Path $qaRepo 'desktop'
$qaSignCommand = 'npx tauri signer sign -f "' + $qaKey + '" -p "" "' + $qaInstaller + '"'
& cmd.exe /d /c "cd /d `"$qaDesktop`" && $qaSignCommand"
if ($LASTEXITCODE -ne 0 -or -not [IO.File]::Exists($qaInstaller + '.sig')) { throw 'Tauri updater signing failed.' }
$qaSignatureText = [IO.File]::ReadAllText($qaInstaller + '.sig').Trim()
$qaSignature = [Convert]::FromBase64String($qaSignatureText)
$qaConfig = [IO.File]::ReadAllText((Join-Path $qaRepo 'desktop\src-tauri\tauri.conf.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
$qaPublicKey = [Convert]::FromBase64String($qaConfig.plugins.updater.pubkey)
if ([Convert]::ToBase64String($qaSignature[2..9]) -ne [Convert]::ToBase64String($qaPublicKey[2..9])) { throw 'Updater signing key does not match the original client.' }
[IO.Directory]::CreateDirectory($qaStage) | Out-Null
$qaName = 'Astrion_0.10.0_x64-setup.exe'
Copy-Item -LiteralPath $qaInstaller -Destination (Join-Path $qaStage $qaName)
Copy-Item -LiteralPath ($qaInstaller + '.sig') -Destination (Join-Path $qaStage ($qaName + '.sig'))
$qaYml = [IO.File]::ReadAllText((Join-Path $qaBuild 'latest.yml'), [Text.Encoding]::UTF8).Replace('Astrion-0.10.0-windows-x64-setup.exe', $qaName)
$qaSha = [Security.Cryptography.SHA512]::Create()
$qaStream = [IO.File]::OpenRead($qaInstaller)
try { $qaSha512 = [Convert]::ToBase64String($qaSha.ComputeHash($qaStream)) } finally { $qaStream.Dispose(); $qaSha.Dispose() }
if (-not $qaYml.Contains($qaSha512)) { throw 'YAML and compiled installer SHA512 differ.' }
[IO.File]::WriteAllText((Join-Path $qaStage 'latest.yml'), $qaYml, $qaUtf8)
$qaRelease = [IO.File]::ReadAllText((Join-Path $qaRepo 'desktop-electron\windows-release.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
[IO.File]::WriteAllText((Join-Path $qaStage 'release-notes-windows.txt'), (($qaRelease.notes | ForEach-Object { '- ' + $_ }) -join "`n") + "`n", $qaUtf8)
$qaSources = @{}
foreach ($qaSource in @('installer-windows.nsh', 'astrion-install-guard.ps1', 'astrion-install-progress.ps1', 'astrion-install-progress.strings.json')) {
    $qaSources[$qaSource] = (Get-FileHash -LiteralPath (Join-Path $qaRepo ('desktop-electron\build\' + $qaSource)) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$qaReceipt = @{ version = '0.10.0'; time = (Get-Date).ToString('o'); installer = $qaName; installerBytes = (Get-Item -LiteralPath $qaInstaller).Length; installerSha256 = (Get-FileHash -LiteralPath $qaInstaller -Algorithm SHA256).Hash.ToLowerInvariant(); installerSha512 = $qaSha512; tauriUpdaterKeyMatches = $true; syntaxChecked = $qaScripts.Count; functionalUpdateTestRun = $false; published = $false; sourceHashes = $qaSources }
$qaReceipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $qaOutput 'package-receipt.json') -Encoding UTF8
$qaReceipt | ConvertTo-Json -Depth 5
Write-Output 'PACKAGE_STAGED_NOT_PUBLISHED_NO_UPDATE_TEST_RUN'
