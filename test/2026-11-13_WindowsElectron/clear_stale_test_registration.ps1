# The diagnostic uninstaller deliberately avoids registry operations. Remove
# only its stale test entries after verifying both name and output path.
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\output\windows-electron\installer-test')).TrimEnd('\') + '\'
foreach ($base in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall', 'HKCU:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
    if (-not (Test-Path -LiteralPath $base)) { continue }
    Get-ChildItem -LiteralPath $base | ForEach-Object {
        $entry = Get-ItemProperty -LiteralPath $_.PSPath
        if ($entry.DisplayName -eq 'Astrion Electron Migration Test') {
            $installKeyPath = "HKCU:\Software\$($_.PSChildName)"
            if (-not (Test-Path -LiteralPath $installKeyPath)) { throw 'Test install registration missing' }
            $registered = Get-ItemProperty -LiteralPath $installKeyPath
            $location = [IO.Path]::GetFullPath($registered.InstallLocation).TrimEnd('\')
            if (-not $location.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected test install location' }
            if (Test-Path -LiteralPath (Join-Path $location 'astrion-electron-migration-test.exe')) { throw 'Test app still exists; refusing stale cleanup' }
            $keyName = $_.PSChildName
            Remove-Item -LiteralPath $_.PSPath -Recurse
            foreach ($installKey in @("HKCU:\Software\$keyName", "HKCU:\Software\WOW6432Node\$keyName")) {
                if (Test-Path -LiteralPath $installKey) {
                    $installed = Get-ItemProperty -LiteralPath $installKey
                    if ($installed.InstallLocation -and [IO.Path]::GetFullPath($installed.InstallLocation).TrimEnd('\') -eq $location) {
                        Remove-Item -LiteralPath $installKey -Recurse
                    }
                }
            }
            Write-Output ('Removed only stale test registration: ' + $keyName)
        }
    }
}
