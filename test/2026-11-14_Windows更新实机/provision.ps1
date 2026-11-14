param([switch]$Elevated)
$ErrorActionPreference = 'Stop'
$qaOutput = $null
$qaCreated = $false
$qaAccount = 'Astrion QA 1114'
$qaDescription = 'Astrion updater QA 20261114 temporary standard'
Write-Host '[Astrion QA] Script started.'
try {
    $qaRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    $qaOutput = Join-Path $qaRepo 'output\windows-update-real-20261114'
    [IO.Directory]::CreateDirectory($qaOutput) | Out-Null
    function Write-QaStage([string]$Stage) {
        $line = '{0} {1}' -f (Get-Date).ToString('o'), $Stage
        Write-Host ('[Astrion QA] ' + $Stage)
        Add-Content -LiteralPath (Join-Path $qaOutput 'provision-stages.log') -Value $line -Encoding UTF8
    }
    $qaIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $qaPrincipal = New-Object Security.Principal.WindowsPrincipal($qaIdentity)
    $qaAdministrator = $qaPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    Write-QaStage ('identity={0}; elevated={1}; is64bit={2}; script={3}' -f $qaIdentity.Name, $qaAdministrator, [Environment]::Is64BitProcess, $PSCommandPath)
    if (-not [Environment]::Is64BitProcess) { throw 'Run this script in 64-bit Windows PowerShell.' }
    if (-not $qaAdministrator) {
        if ($Elevated) { throw 'Administrator token is absent. No account was created.' }
        Write-QaStage 'Requesting Windows administrator approval; no account action has run yet.'
        $qaArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Elevated'
        $qaChild = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Verb RunAs -ArgumentList $qaArguments -WorkingDirectory $qaRepo -PassThru
        if (-not $qaChild.WaitForExit(90000)) { throw 'Administrator stage has not finished after 90 seconds; no installer will be started.' }
        $qaChild.Refresh()
        if ($qaChild.ExitCode -ne 0) { throw "Administrator stage exited with code $($qaChild.ExitCode). See provision-error.txt." }
        Get-Content -LiteralPath (Join-Path $qaOutput 'account.json')
        exit 0
    }
    $qaExisting = Get-LocalUser | Where-Object { $_.Name -eq $qaAccount }
    if ($qaExisting) { throw 'The test account already exists. Refusing to reset its password or change it automatically.' }
    Write-QaStage 'Preparing private credential storage.'
    $qaCredentialDirectory = Join-Path $env:LOCALAPPDATA 'AstrionUpdateQA-20261114'
    [IO.Directory]::CreateDirectory($qaCredentialDirectory) | Out-Null
    $qaAcl = Get-Acl -LiteralPath $qaCredentialDirectory
    $qaAcl.SetAccessRuleProtection($true, $false)
    $qaAcl.SetOwner($qaIdentity.User)
    $qaUserRule = New-Object Security.AccessControl.FileSystemAccessRule -ArgumentList @($qaIdentity.User, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $qaSystemSid = New-Object Security.Principal.SecurityIdentifier -ArgumentList 'S-1-5-18'
    $qaSystemRule = New-Object Security.AccessControl.FileSystemAccessRule -ArgumentList @($qaSystemSid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $qaAcl.AddAccessRule($qaUserRule)
    $qaAcl.AddAccessRule($qaSystemRule)
    Set-Acl -LiteralPath $qaCredentialDirectory -AclObject $qaAcl
    $qaRandom = New-Object byte[] 36
    $qaRng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $qaRng.GetBytes($qaRandom) } finally { $qaRng.Dispose() }
    $qaSecret = [Convert]::ToBase64String($qaRandom) + 'aA9!'
    $qaSecure = ConvertTo-SecureString $qaSecret -AsPlainText -Force
    $qaSecret = $null
    Write-QaStage 'Creating the temporary standard user.'
    $qaExpiry = (Get-Date).AddDays(2)
    $qaUser = New-LocalUser -Name $qaAccount -Password $qaSecure -Description $qaDescription -AccountExpires $qaExpiry
    $qaCreated = $true
    $qaUsersGroup = Get-LocalGroup -SID 'S-1-5-32-545'
    $qaMembers = Get-LocalGroupMember -Group $qaUsersGroup
    if (-not ($qaMembers | Where-Object { $_.SID.Value -eq $qaUser.SID.Value })) {
        Add-LocalGroupMember -Group $qaUsersGroup -Member $qaUser
    }
    Write-QaStage 'Saving the credential with Windows DPAPI; the password is never printed.'
    $qaCredential = New-Object Management.Automation.PSCredential -ArgumentList @("$env:COMPUTERNAME\$qaAccount", $qaSecure)
    $qaCredential | Export-Clixml -LiteralPath (Join-Path $qaCredentialDirectory 'credential.xml')
    @{ account = $qaAccount; sid = $qaUser.SID.Value; standardUser = $true; expires = $qaExpiry.ToString('o'); description = $qaDescription; credentialOwner = $qaIdentity.Name; credentialStorage = 'Windows DPAPI, private ACL, outside repository' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $qaOutput 'account.json') -Encoding UTF8
    Write-QaStage 'SUCCESS. Account prepared. No installer was launched and no application was stopped.'
    Get-Content -LiteralPath (Join-Path $qaOutput 'account.json')
    exit 0
} catch {
    $qaFailure = ($_ | Format-List * -Force | Out-String)
    [Console]::Error.WriteLine('[Astrion QA] FAILED: ' + $_.Exception.Message)
    if ($qaOutput) {
        try { $qaFailure | Set-Content -LiteralPath (Join-Path $qaOutput 'provision-error.txt') -Encoding UTF8 } catch { }
    }
    if ($qaCreated) {
        try { Remove-LocalUser -Name $qaAccount; [Console]::Error.WriteLine('[Astrion QA] Removed the incomplete test account created by this invocation.') } catch { [Console]::Error.WriteLine('[Astrion QA] Incomplete test account could not be removed; inspect before retrying.') }
    }
    exit 1
}
