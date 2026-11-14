param(
    [Parameter(Mandatory=$true)][ValidateSet('Probe','InstallLegacy','LaunchLegacy','Observe','MonitorUpdate','PrepareElectron','CrossCopy','FinalizeTest','VerifySources','PrepareManualLegacy')][string]$Mode
)
$ErrorActionPreference = 'Stop'
$qaRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$qaOutput = Join-Path $qaRepo 'output\windows-update-real-20261114'
$qaReport = Get-Content -LiteralPath (Join-Path $qaOutput 'account.json') -Raw | ConvertFrom-Json
$qaCredentialPath = Join-Path $env:LOCALAPPDATA 'AstrionUpdateQA-20261114\credential.xml'
$qaCredential = Import-Clixml -LiteralPath $qaCredentialPath
if ($qaCredential.UserName -ne "$env:COMPUTERNAME\Astrion QA 1114") { throw 'Unexpected test credential identity.' }
$qaUser = Get-LocalUser -Name $qaReport.account
if ($qaUser.SID.Value -ne $qaReport.sid) { throw 'Test account SID changed.' }
$qaOwner = [Security.Principal.WindowsIdentity]::GetCurrent()
$qaProtectedPath = Join-Path $qaOutput 'protected-processes.json'
if (-not (Test-Path -LiteralPath $qaProtectedPath)) {
    $qaProtected = @(Get-Process | Where-Object { $_.ProcessName -match '^astrion-(desktop|backend|windows-helper)$' } | ForEach-Object { @{ pid = $_.Id; name = $_.ProcessName; started = $_.StartTime.ToUniversalTime().ToString('o'); path = $_.Path } })
    if ($qaProtected.Count -eq 0) { throw 'Cannot establish the protected application baseline.' }
    @{ ownerSid = $qaOwner.User.Value; processes = $qaProtected } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $qaProtectedPath -Encoding UTF8
}
function Assert-ProtectedProcesses {
    $baseline = Get-Content -LiteralPath $qaProtectedPath -Raw | ConvertFrom-Json
    foreach ($row in $baseline.processes) {
        $process = Get-Process -Id $row.pid -ErrorAction Stop
        if ($process.StartTime.ToUniversalTime().ToString('o') -ne $row.started -or $process.Path -ne $row.path) { throw "Protected process identity changed: $($row.pid)" }
    }
    Write-Output ('PROTECTED_PROCESS_COUNT ' + @($baseline.processes).Count)
}
Assert-ProtectedProcesses
$qaChildScript = Join-Path $PSScriptRoot 'isolated-entry.cmd'
$qaProfileKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\' + $qaReport.sid
$qaProfile = [Environment]::ExpandEnvironmentVariables((Get-ItemProperty $qaProfileKey).ProfileImagePath)
$qaInvocation = [Guid]::NewGuid().ToString('N')
$qaArgs = '/d /s /c ""' + $qaChildScript + '" ' + $Mode + ' ' + $qaReport.sid + ' "' + $qaProfile + '" ' + $qaInvocation + '"'
$qaStdout = Join-Path $qaOutput ($Mode + '-child.stdout.log')
$qaStderr = Join-Path $qaOutput ($Mode + '-child.stderr.log')
$qaProcess = Start-Process -FilePath "$env:SystemRoot\System32\cmd.exe" -Credential $qaCredential -LoadUserProfile -WorkingDirectory $qaRepo -ArgumentList $qaArgs -RedirectStandardOutput $qaStdout -RedirectStandardError $qaStderr -PassThru
$qaProcessHandle = $qaProcess.Handle
Write-Output ('ISOLATED_HELPER_PID ' + $qaProcess.Id)
$qaTimeout = if ($Mode -eq 'PrepareManualLegacy') { 1800000 } elseif ($Mode -eq 'InstallLegacy') { 1200000 } elseif ($Mode -eq 'MonitorUpdate') { 1320000 } else { 90000 }
if (-not $qaProcess.WaitForExit($qaTimeout)) { throw "Isolated helper still running after timeout; inspect PID $($qaProcess.Id) before another operation." }
if (Test-Path -LiteralPath $qaStdout) { Get-Content -LiteralPath $qaStdout -Tail 50 }
if (Test-Path -LiteralPath $qaStderr) { Get-Content -LiteralPath $qaStderr -Tail 50 }
Assert-ProtectedProcesses
$qaCompletion = Get-Content -LiteralPath (Join-Path $qaOutput ($Mode + '-completion.json')) -Raw | ConvertFrom-Json
if ($qaCompletion.invocation -ne $qaInvocation -or -not $qaCompletion.success) { throw 'Missing or failed completion receipt for this invocation.' }
Write-Output ('ISOLATED_COMPLETION_VERIFIED ' + $qaInvocation)
