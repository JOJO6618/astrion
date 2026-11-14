$ErrorActionPreference = 'Stop'
$qaRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$qaOutput = Join-Path $qaRepo 'output\windows-update-progress-20261114'
$qaHttp = Join-Path $qaOutput 'public-http'
[IO.Directory]::CreateDirectory($qaHttp) | Out-Null
$qaPackage = [IO.File]::ReadAllText((Join-Path $qaOutput 'package-receipt.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
$qaServer = [IO.File]::ReadAllText((Join-Path $qaOutput 'server-publish-receipt.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
if (-not $qaServer.success -or $qaServer.installerSha256 -ne $qaPackage.installerSha256) { throw 'Server publication not confirmed.' }
$qaStage = Join-Path $qaOutput 'release-0.10.0'
$qaBase = 'https://astrion.cyjai.com/downloads/'
foreach ($qaName in @('latest.yml', 'latest-windows-x86_64.json', 'downloads.json', ($qaPackage.installer + '.sig'))) {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 -Uri ($qaBase + $qaName) -OutFile (Join-Path $qaHttp $qaName)
}
$qaLegacy = [IO.File]::ReadAllText((Join-Path $qaHttp 'latest-windows-x86_64.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
$qaDownloads = [IO.File]::ReadAllText((Join-Path $qaHttp 'downloads.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
$qaYaml = [IO.File]::ReadAllText((Join-Path $qaHttp 'latest.yml'), [Text.Encoding]::UTF8)
$qaSignature = [IO.File]::ReadAllText((Join-Path $qaStage ($qaPackage.installer + '.sig'))).Trim()
$qaNotes = [IO.File]::ReadAllText((Join-Path $qaStage 'release-notes-windows.txt'), [Text.Encoding]::UTF8).Trim()
if ($qaLegacy.version -ne '0.10.0' -or $qaLegacy.platforms.'windows-x86_64'.signature -ne $qaSignature -or $qaLegacy.platforms.'windows-x86_64'.url -ne ($qaBase + $qaPackage.installer) -or $qaLegacy.notes -ne $qaNotes) { throw 'Public old-client manifest does not match the approved release.' }
if ([IO.File]::ReadAllText((Join-Path $qaHttp ($qaPackage.installer + '.sig'))).Trim() -ne $qaSignature) { throw 'Public signature file differs.' }
if ($qaYaml.Trim() -ne [IO.File]::ReadAllText((Join-Path $qaStage 'latest.yml'), [Text.Encoding]::UTF8).Trim()) { throw 'Public Electron manifest differs.' }
$qaWin = $qaDownloads.platforms.'windows-x64'
if ($qaWin.version -ne '0.10.0' -or $qaWin.file -ne $qaPackage.installer -or $qaWin.size_bytes -ne $qaPackage.installerBytes -or $qaWin.notes -ne $qaNotes) { throw 'Public website Windows entry differs.' }
$qaHead = Invoke-WebRequest -UseBasicParsing -Method Head -TimeoutSec 60 -Uri ($qaBase + $qaPackage.installer)
if ([int]$qaHead.StatusCode -ne 200 -or [long]$qaHead.Headers['Content-Length'] -ne $qaPackage.installerBytes) { throw 'Public installer HTTP status or size differs.' }
$qaReceipt = @{ success = $true; published = $true; publicHttpVerified = $true; time = (Get-Date).ToString('o'); version = '0.10.0'; installerBytes = $qaPackage.installerBytes; installerSha256 = $qaServer.installerSha256; installerHeadStatus = [int]$qaHead.StatusCode; installerContentLength = [long]$qaHead.Headers['Content-Length']; legacySignatureMatches = $true; electronYamlMatches = $true; notesMatch = $true; websiteWindowsEntryMatches = $true; macosUnchanged = $qaServer.macosUnchanged; macosVersion = $qaDownloads.platforms.'macos-arm64'.version; backup = $qaServer.backup; functionalUpdateTestRun = $false }
$qaReceipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $qaOutput 'publication-verification.json') -Encoding UTF8
$qaReceipt | ConvertTo-Json -Depth 5
Write-Output 'PUBLIC_RELEASE_VERIFIED_USER_CAN_CLICK_UPDATE'
