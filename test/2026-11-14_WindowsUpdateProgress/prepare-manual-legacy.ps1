param([Parameter(Mandatory=$true)][string]$ExpectedSid)
$ErrorActionPreference = 'Stop'
$qaIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($qaIdentity.User.Value -ne $ExpectedSid -or $ExpectedSid -ne 'S-1-5-21-1133143854-1543063607-2493749742-1010') { throw 'Unexpected test account.' }
$qaRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$qaOutput = Join-Path $qaRepo 'output\windows-update-progress-20261114'
[IO.Directory]::CreateDirectory($qaOutput) | Out-Null
$qaInstall = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Astrion'
$qaExecutable = Join-Path $qaInstall 'astrion-desktop.exe'
$qaPrefix = $qaInstall.TrimEnd('\') + '\'
$qaRunning = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($qaPrefix, [StringComparison]::OrdinalIgnoreCase) })
if ($qaRunning.Count) { throw 'The test installation is running. Inspect it instead of stopping it automatically.' }
if (Test-Path -LiteralPath $qaExecutable) {
    $qaVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($qaExecutable).ProductVersion
    if ($qaVersion -ne '0.10.1.0') { throw ('Unexpected previous test installation: ' + $qaVersion) }
    $qaUninstallers = @(Get-ChildItem -LiteralPath $qaInstall -Filter 'Uninstall *.exe' -File)
    if ($qaUninstallers.Count -ne 1) { throw 'Expected exactly one Electron uninstaller in the test installation.' }
    $qaUninstaller = $qaUninstallers[0].FullName
    Write-Output ('REMOVING_ONLY_THE_PREVIOUS_TEST_INSTALLATION_KEEPING_USER_DATA ' + $qaUninstaller)
    $qaUninstall = Start-Process -FilePath $qaUninstaller -ArgumentList '/S' -PassThru
    $null = $qaUninstall.Handle
    if (-not $qaUninstall.WaitForExit(600000)) { throw 'Test uninstaller is still running. No termination attempted.' }
    $qaDeadline = (Get-Date).AddMinutes(3)
    while ([IO.File]::Exists($qaExecutable)) {
        if ((Get-Date) -gt $qaDeadline) { throw 'Test executable remains after uninstall; original installer was not started.' }
        Start-Sleep -Milliseconds 500
    }
}
$qaInstaller = Join-Path $qaRepo 'cache\Astrion_0.9.3_x64-setup.exe'
if ((Get-FileHash -LiteralPath $qaInstaller -Algorithm SHA256).Hash -ne '5DB090FEAC0507875FFA87C13372488679112CCF6A97BED98754FE4C706EC5C8') { throw 'Original 0.9.3 installer hash changed.' }
Write-Output 'INSTALLING_ORIGINAL_093_IN_TEST_ACCOUNT'
$qaLegacy = Start-Process -FilePath $qaInstaller -ArgumentList '/S' -PassThru
$null = $qaLegacy.Handle
if (-not $qaLegacy.WaitForExit(1100000)) { throw 'Original installer still running. No termination attempted.' }
if ($qaLegacy.ExitCode -ne 0) { throw ('Original installer failed: ' + $qaLegacy.ExitCode) }
$qaVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($qaExecutable).ProductVersion
if ($qaVersion -notmatch '^0\.9\.3(?:\.|$)') { throw ('Installed version is not 0.9.3: ' + $qaVersion) }
# Do not launch any updater or click anything. The user will perform the update.
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = ''
$qaApp = Start-Process -FilePath $qaExecutable -WorkingDirectory $qaInstall -PassThru
@{ time = (Get-Date).ToString('o'); sid = $ExpectedSid; pid = $qaApp.Id; version = $qaVersion; path = $qaExecutable; originalInstallerUnmodified = $true; originalUpdateSourceUnmodified = $true; userWillClickUpdate = $true; upgradeNotStarted = $true } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $qaOutput 'manual-legacy-launch.json') -Encoding UTF8
Write-Output ('ORIGINAL_093_STARTED_FOR_USER PID=' + $qaApp.Id)
