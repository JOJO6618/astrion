param(
    [Parameter(Mandatory=$true)][ValidateSet('Probe','InstallLegacy','LaunchLegacy','Observe','MonitorUpdate','PrepareElectron','CrossCopy','FinalizeTest','VerifySources','PrepareManualLegacy')][string]$Mode,
    [Parameter(Mandatory=$true)][string]$ExpectedSid,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$InvocationId
)
$ErrorActionPreference = 'Stop'
$qaRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$qaOutput = Join-Path $qaRepo 'output\windows-update-real-20261114'
try {
    $qaIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $qaPrincipal = New-Object Security.Principal.WindowsPrincipal($qaIdentity)
    if ($qaIdentity.User.Value -ne $ExpectedSid -or $qaPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Refusing to operate outside the expected standard test account.' }
    $qaBaseline = Get-Content -LiteralPath (Join-Path $qaOutput 'protected-processes.json') -Raw | ConvertFrom-Json
    if ($qaIdentity.User.Value -eq $qaBaseline.ownerSid) { throw 'Test account equals protected application owner.' }
    $env:USERPROFILE = [Environment]::GetFolderPath('UserProfile')
    $env:LOCALAPPDATA = [Environment]::GetFolderPath('LocalApplicationData')
    $env:APPDATA = [Environment]::GetFolderPath('ApplicationData')
    if ($env:USERPROFILE -notmatch '\\Astrion QA 1114(?:\.|$)' -or $env:LOCALAPPDATA -notlike ($env:USERPROFILE + '\*')) { throw "Unexpected isolated profile: $env:USERPROFILE" }
    $env:TEMP = Join-Path $env:LOCALAPPDATA 'Temp'
    $env:TMP = $env:TEMP
    [IO.Directory]::CreateDirectory($env:TEMP) | Out-Null
    $env:ASTRION_IGNORE_DOTENV = '1'
    $qaWorkspace = Join-Path $qaOutput 'workspace'
    [IO.Directory]::CreateDirectory($qaWorkspace) | Out-Null
    $env:ASTRION_DESKTOP_INITIAL_WORKSPACE = $qaWorkspace
    $qaInstall = Join-Path $env:LOCALAPPDATA 'Astrion'
    $qaState = @{ identity = $qaIdentity.Name; sid = $qaIdentity.User.Value; profile = $env:USERPROFILE; localAppData = $env:LOCALAPPDATA; temp = $env:TEMP; install = $qaInstall; elevated = $false; mode = $Mode; time = (Get-Date).ToString('o') }
    $qaState | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $qaOutput ($Mode + '-identity.json')) -Encoding UTF8
    $qaState | ConvertTo-Json
    if ($Mode -eq 'Probe') {
        $qaMarker = Join-Path $env:TEMP 'astrion-real-update-probe.txt'
        'isolated standard user' | Set-Content -LiteralPath $qaMarker -Encoding ASCII
        if (-not (Test-Path -LiteralPath $qaMarker)) { throw 'Test profile is not writable.' }
        Write-Output 'PROBE_SUCCESS'
    } elseif ($Mode -eq 'InstallLegacy') {
        if (Test-Path -LiteralPath (Join-Path $qaInstall 'astrion-desktop.exe')) { throw 'Test install already exists; inspect instead of overwriting.' }
        $qaInstaller = Join-Path $qaRepo 'cache\Astrion_0.9.3_x64-setup.exe'
        $qaHash = (Get-FileHash -LiteralPath $qaInstaller -Algorithm SHA256).Hash
        if ($qaHash -ne '5DB090FEAC0507875FFA87C13372488679112CCF6A97BED98754FE4C706EC5C8') { throw 'Original 0.9.3 installer hash changed.' }
        Write-Output ('START_ORIGINAL_093_INSTALLER ' + $qaInstaller)
        $qaInstallerProcess = Start-Process -FilePath $qaInstaller -ArgumentList '/S' -PassThru
        $qaInstallerHandle = $qaInstallerProcess.Handle
        Write-Output ('INSTALLER_PID ' + $qaInstallerProcess.Id)
        if (-not $qaInstallerProcess.WaitForExit(1100000)) { throw "Installer is still running: $($qaInstallerProcess.Id). No termination was attempted." }
        Write-Output ('INSTALLER_EXIT ' + $qaInstallerProcess.ExitCode)
        if ($qaInstallerProcess.ExitCode -ne 0) { throw 'Original installer failed.' }
        $qaExecutable = Join-Path $qaInstall 'astrion-desktop.exe'
        $qaVersion = (Get-Item -LiteralPath $qaExecutable).VersionInfo.ProductVersion
        if ($qaVersion -notmatch '^0\.9\.3(?:\.|$)') { throw "Unexpected installed version: $qaVersion" }
        Write-Output ('INSTALLED_LEGACY_VERSION ' + $qaVersion)
    } elseif ($Mode -eq 'LaunchLegacy') {
        $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = '--remote-debugging-address=127.0.0.1 --remote-debugging-port=19973'
        $qaExecutable = Join-Path $qaInstall 'astrion-desktop.exe'
        $qaVersion = (Get-Item -LiteralPath $qaExecutable).VersionInfo.ProductVersion
        if ($qaVersion -notmatch '^0\.9\.3(?:\.|$)') { throw "Refusing to launch non-legacy executable: $qaVersion" }
        $qaApp = Start-Process -FilePath $qaExecutable -WorkingDirectory $qaInstall -PassThru
        @{ pid = $qaApp.Id; executable = $qaExecutable; version = $qaVersion; cdp = 'http://127.0.0.1:19973'; sid = $qaIdentity.User.Value } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $qaOutput 'legacy-launch.json') -Encoding UTF8
        Write-Output ('LEGACY_APP_PID ' + $qaApp.Id)
    } elseif ($Mode -eq 'MonitorUpdate') {
        & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'dialog-monitor.ps1') -ExpectedSid $ExpectedSid -OutputPath $qaOutput -MaxMinutes 20
        if ($LASTEXITCODE -ne 0) { throw "Dialog monitor failed: $LASTEXITCODE" }
    } elseif ($Mode -eq 'PrepareElectron') {
        & (Join-Path $PSScriptRoot 'prepare-electron.ps1') -ExpectedSid $ExpectedSid
    } elseif ($Mode -eq 'PrepareManualLegacy') {
        & (Join-Path $PSScriptRoot '..\2026-11-14_WindowsUpdateProgress\prepare-manual-legacy.ps1') -ExpectedSid $ExpectedSid
    } elseif ($Mode -eq 'CrossCopy') {
        & (Join-Path $PSScriptRoot 'cross-copy.ps1') -ExpectedSid $ExpectedSid
    } elseif ($Mode -eq 'FinalizeTest') {
        & (Join-Path $PSScriptRoot 'finalize-test.ps1') -ExpectedSid $ExpectedSid
    } elseif ($Mode -eq 'VerifySources') {
        $qaYaml = [IO.File]::ReadAllText((Join-Path $qaInstall 'resources\app-update.yml'))
        $qaBackendSource = [IO.File]::ReadAllText((Join-Path $qaInstall 'resources\runtime\backend\server\status\desktop_update.py'))
        if (-not $qaYaml.Contains('https://astrion.cyjai.com/downloads/') -or $qaYaml.Contains('19976') -or -not $qaBackendSource.Contains('https://astrion.cyjai.com/downloads') -or $qaBackendSource.Contains('19976')) { throw 'Temporary update source remains in the test installation.' }
        @{ success = $true; invocation = $InvocationId; sid = $ExpectedSid; officialUpdateSourcesVerified = $true; time = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $qaOutput 'official-sources-verification.json') -Encoding UTF8
        Write-Output 'OFFICIAL_UPDATE_SOURCES_VERIFIED'
    } elseif ($Mode -eq 'Observe') {
        foreach ($qaRelative in @('astrion-desktop.exe','resources.pak','snapshot_blob.bin','v8_context_snapshot.bin','resources\app.asar','resources\runtime\python\python.exe','debug.log','install-incomplete.log')) {
            $qaFile = Join-Path $qaInstall $qaRelative
            if (Test-Path -LiteralPath $qaFile) {
                Get-Item -LiteralPath $qaFile | Select-Object FullName,Length,LastWriteTime | Format-List
                if ($qaRelative -like '*.log') { Get-Content -LiteralPath $qaFile -Tail 35 }
            } else { Write-Output ('MISSING ' + $qaRelative) }
        }
        foreach ($qaFile in @((Join-Path $env:TEMP 'astrion-install-guard.log'), (Join-Path $env:TEMP 'astrion-install-guard.state'), (Join-Path $env:USERPROFILE '.astrion\astrion-desktop\logs\desktop-update.log'))) {
            if (Test-Path -LiteralPath $qaFile) { Write-Output ('LOG ' + $qaFile); Get-Content -LiteralPath $qaFile -Tail 50 }
        }
        Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($qaInstall + '\', [StringComparison]::OrdinalIgnoreCase) } | Select-Object Id,ProcessName,StartTime,Path,MainWindowHandle,MainWindowTitle | Format-List
        $qaGatewayFile = Join-Path $env:USERPROFILE '.astrion\quick-entry-gateway.json'
        if (Test-Path -LiteralPath $qaGatewayFile) {
            $qaGateway = Get-Content -LiteralPath $qaGatewayFile -Raw | ConvertFrom-Json
            $qaGateway | ConvertTo-Json
            $qaApp = Get-Process -Id $qaGateway.pid
            if (-not $qaApp.Path.StartsWith($qaInstall + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Gateway process is outside the test installation.' }
            $qaTokenPath = Join-Path $qaGateway.dataRoot 'host\data\host_api_token'
            $qaToken = [IO.File]::ReadAllText($qaTokenPath).Trim()
            $qaWorkspaceResponse = Invoke-RestMethod -Uri ('http://127.0.0.1:' + $qaGateway.port + '/api/host/workspaces') -Headers @{ Authorization = ('Bearer ' + $qaToken) } -TimeoutSec 10
            $qaToken = $null
            $qaHealth = @{ endpoint = '/api/host/workspaces'; success = $qaWorkspaceResponse.success }
            if (-not $qaHealth.success) { throw 'Authenticated test backend request failed.' }
            $qaObservedVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($qaApp.Path).ProductVersion
            $qaSourceRoot = if ($qaObservedVersion -match '^0\.10\.1(?:\.|$)') { Join-Path $qaOutput 'build-next\win-unpacked' } else { Join-Path $qaRepo 'output\windows-electron\build\win-unpacked' }
            $qaArchives = @(Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter 'ns*.tmp' | ForEach-Object { $candidate = Join-Path $_.FullName 'app-64.zip'; if (Test-Path -LiteralPath $candidate) { Get-Item -LiteralPath $candidate } })
            $qaPayloadCheck = $null
            if ($qaArchives.Count -gt 0) {
                Add-Type -AssemblyName System.IO.Compression.FileSystem
                $qaArchive = $qaArchives | Sort-Object LastWriteTime -Descending | Select-Object -First 1
                $qaZip = [IO.Compression.ZipFile]::OpenRead($qaArchive.FullName)
                try {
                    $qaMissing = @(); $qaWrongSize = @(); $qaFiles = 0
                    foreach ($qaEntry in $qaZip.Entries) {
                        if ($qaEntry.FullName.EndsWith('/')) { continue }
                        $qaFiles++
                        $qaTarget = Join-Path $qaInstall $qaEntry.FullName
                        if (-not [IO.File]::Exists($qaTarget)) { $qaMissing += $qaEntry.FullName }
                        elseif (([IO.FileInfo]$qaTarget).Length -ne $qaEntry.Length) { $qaWrongSize += $qaEntry.FullName }
                    }
                    $qaPayloadCheck = @{ files = $qaFiles; missing = $qaMissing; wrongSize = $qaWrongSize; archive = $qaArchive.FullName }
                } finally { $qaZip.Dispose() }
            } else {
                $qaMissing = @(); $qaWrongSize = @(); $qaFiles = 0
                foreach ($qaSourceFile in [IO.Directory]::EnumerateFiles($qaSourceRoot, '*', [IO.SearchOption]::AllDirectories)) {
                    $qaRelative = $qaSourceFile.Substring($qaSourceRoot.Length + 1)
                    $qaTarget = Join-Path $qaInstall $qaRelative
                    $qaFiles++
                    if (-not [IO.File]::Exists($qaTarget)) { $qaMissing += $qaRelative }
                    elseif (([IO.FileInfo]$qaTarget).Length -ne ([IO.FileInfo]$qaSourceFile).Length) { $qaWrongSize += $qaRelative }
                }
                $qaPayloadCheck = @{ files = $qaFiles; missing = $qaMissing; wrongSize = $qaWrongSize; source = $qaSourceRoot }
            }
            $qaCriticalHashes = @()
            foreach ($qaCritical in @('astrion-desktop.exe','resources\app.asar')) {
                $qaInstalledHash = (Get-FileHash -LiteralPath (Join-Path $qaInstall $qaCritical) -Algorithm SHA256).Hash
                $qaSourceHash = (Get-FileHash -LiteralPath (Join-Path $qaSourceRoot $qaCritical) -Algorithm SHA256).Hash
                $qaCriticalHashes += @{ file = $qaCritical; installed = $qaInstalledHash; source = $qaSourceHash; matches = ($qaInstalledHash -eq $qaSourceHash) }
            }
            $qaSentinelPreserved = $null
            $qaLaunchInfoPath = Join-Path $qaOutput 'electron-launch.json'
            if (Test-Path -LiteralPath $qaLaunchInfoPath) {
                $qaLaunchInfo = Get-Content -LiteralPath $qaLaunchInfoPath -Raw | ConvertFrom-Json
                $qaSentinelPreserved = ([IO.File]::ReadAllText($qaLaunchInfo.sentinelPath) -eq $qaLaunchInfo.sentinel)
            }
            $qaMigration = @{ time = (Get-Date).ToString('o'); executableVersion = $qaObservedVersion; appPid = $qaApp.Id; started = $qaApp.StartTime.ToString('o'); path = $qaApp.Path; gateway = $qaGateway; health = $qaHealth; payload = $qaPayloadCheck; criticalHashes = $qaCriticalHashes; sentinelPreserved = $qaSentinelPreserved; legacyUninstallExists = (Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Astrion') }
            $qaMigration | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $qaOutput 'migration-observation.json') -Encoding UTF8
            $qaMigration | ConvertTo-Json -Depth 8
        }
    }
    @{ invocation = $InvocationId; success = $true; mode = $Mode; sid = $qaIdentity.User.Value; time = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $qaOutput ($Mode + '-completion.json')) -Encoding UTF8
    exit 0
} catch {
    @{ invocation = $InvocationId; success = $false; mode = $Mode; error = $_.Exception.Message; time = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $qaOutput ($Mode + '-completion.json')) -Encoding UTF8
    $qaError = $_ | Format-List * -Force | Out-String
    $qaError | Set-Content -LiteralPath (Join-Path $qaOutput ($Mode + '-error.txt')) -Encoding UTF8
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
