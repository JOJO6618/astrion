$ErrorActionPreference = 'Stop'
$qaRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$qaOutput = Join-Path $qaRepo 'output\windows-update-progress-20261114'
$qaStage = Join-Path $qaOutput 'release-0.10.0'
$qaReceipt = [IO.File]::ReadAllText((Join-Path $qaOutput 'package-receipt.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
$qaUtf8 = New-Object Text.UTF8Encoding $false
$qaInstaller = Join-Path $qaStage $qaReceipt.installer
if ((Get-FileHash -LiteralPath $qaInstaller -Algorithm SHA256).Hash.ToLowerInvariant() -ne $qaReceipt.installerSha256 -or (Get-Item -LiteralPath $qaInstaller).Length -ne $qaReceipt.installerBytes) { throw 'Approved installer changed.' }
foreach ($qaSource in $qaReceipt.sourceHashes.PSObject.Properties) {
    if ((Get-FileHash -LiteralPath (Join-Path $qaRepo ('desktop-electron\build\' + $qaSource.Name)) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $qaSource.Value) { throw ('Packaged source no longer matches: ' + $qaSource.Name) }
}
$qaRelease = [IO.File]::ReadAllText((Join-Path $qaRepo 'desktop-electron\windows-release.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
$qaNotes = (($qaRelease.notes | ForEach-Object { '- ' + $_ }) -join "`n") + "`n"
if ($qaNotes.Length -gt 2000) { throw 'Release notes exceed the server limit.' }
[IO.File]::WriteAllText((Join-Path $qaStage 'release-notes-windows.txt'), $qaNotes, $qaUtf8)
$qaFiles = @($qaReceipt.installer, ($qaReceipt.installer + '.sig'), 'release-notes-windows.txt', 'latest.yml')
$qaHashes = @{}
foreach ($qaName in $qaFiles) { $qaHashes[$qaName] = (Get-FileHash -LiteralPath (Join-Path $qaStage $qaName) -Algorithm SHA256).Hash.ToLowerInvariant() }
$qaExpected = @{ files = $qaHashes; installerBytes = $qaReceipt.installerBytes; installerSha256 = $qaReceipt.installerSha256; installerSha512 = $qaReceipt.installerSha512; previousInstallerSha256 = '0b33808c1d5badeb111bbfd81272cc5c5eb41cbceff92dd3060f130d7a9da4b8' }
$qaExpectedPath = Join-Path $qaOutput 'expected.json'
[IO.File]::WriteAllText($qaExpectedPath, ($qaExpected | ConvertTo-Json -Depth 5), $qaUtf8)
$qaRemoteHost = '59.110.19.30'
$qaRemote = '/var/www/astrion/.release-work/windows-progress-' + [Guid]::NewGuid().ToString('N')
[IO.File]::WriteAllText((Join-Path $qaOutput 'remote-work-path.txt'), $qaRemote, $qaUtf8)
& ssh -o BatchMode=yes -o ConnectTimeout=15 $qaRemoteHost 'python3 --version'
if ($LASTEXITCODE -ne 0) { throw 'Server Python probe failed.' }
& ssh -o BatchMode=yes -o ConnectTimeout=15 $qaRemoteHost ("mkdir -p '" + $qaRemote + "/payload'")
if ($LASTEXITCODE -ne 0) { throw 'Remote staging directory creation failed.' }
Write-Output ('UPLOADING_APPROVED_INSTALLER bytes=' + $qaReceipt.installerBytes)
$qaLocalFiles = @($qaFiles | ForEach-Object { Join-Path $qaStage $_ })
& scp -o BatchMode=yes -o ConnectTimeout=15 @qaLocalFiles ("${qaRemoteHost}:" + $qaRemote + '/payload/')
if ($LASTEXITCODE -ne 0) { throw 'Upload failed; live files were not changed.' }
& scp -o BatchMode=yes -o ConnectTimeout=15 (Join-Path $PSScriptRoot 'publish-remote.py') $qaExpectedPath ("${qaRemoteHost}:" + $qaRemote + '/')
if ($LASTEXITCODE -ne 0) { throw 'Deployment metadata upload failed; live files were not changed.' }
Write-Output 'UPLOADED_BACKING_UP_AND_SWITCHING_SERVER_FILES'
$qaServerOutput = & ssh -o BatchMode=yes -o ConnectTimeout=15 $qaRemoteHost ("python3 '" + $qaRemote + "/publish-remote.py'")
if ($LASTEXITCODE -ne 0) { throw 'Server publication failed; inspect remote staging logs before retrying.' }
$qaServerOutput | Write-Output
$qaServerReceipt = ($qaServerOutput -join "`n") | ConvertFrom-Json
if (-not $qaServerReceipt.success -or $qaServerReceipt.installerSha256 -ne $qaReceipt.installerSha256) { throw 'Server publication receipt mismatch.' }
[IO.File]::WriteAllText((Join-Path $qaOutput 'server-publish-receipt.json'), ($qaServerReceipt | ConvertTo-Json -Depth 5), $qaUtf8)
Write-Output 'SERVER_PUBLISHED_PENDING_PUBLIC_HTTP_VERIFICATION'
