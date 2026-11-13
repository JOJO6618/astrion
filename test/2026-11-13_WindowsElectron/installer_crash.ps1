$ErrorActionPreference = 'Stop'
Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; StartTime=(Get-Date).AddHours(-2)} -ErrorAction SilentlyContinue |
    Where-Object { $_.Message -like '*Astrion-Electron-Test-0.10.0.exe*' -or $_.Message -like '*astrion-electron-migration-test*' } |
    Select-Object -First 3 | ForEach-Object { Write-Output $_.Message }
