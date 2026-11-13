$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$runner = 'test\2026-11-13_WindowsElectron\installed_update.mjs'
Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" | Where-Object {
    $_.CommandLine -and ($_.CommandLine.Contains($runner) -or $_.CommandLine.Contains($runner.Replace('\', '/')))
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force
    Write-Output 'Stopped only the isolated installed_update.mjs test runner.'
}
& (Join-Path $PSScriptRoot 'stop_failed_test_installer.ps1')
