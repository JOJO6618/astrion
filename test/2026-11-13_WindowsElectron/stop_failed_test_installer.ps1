$ErrorActionPreference = 'Stop'
$expected = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'astrion-electron-migration-test-updater\pending\Astrion-Electron-Test-0.10.1.exe'))
Get-CimInstance Win32_Process -Filter "Name = 'Astrion-Electron-Test-0.10.1.exe'" | ForEach-Object {
    if ($_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath).Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
        Write-Output 'Stopped only the failed isolated test updater.'
    }
}
