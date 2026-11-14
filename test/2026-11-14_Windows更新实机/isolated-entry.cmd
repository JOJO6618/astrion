@echo off
setlocal
set "USERPROFILE=%~3"
set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"
set "APPDATA=%USERPROFILE%\AppData\Roaming"
set "TEMP=%LOCALAPPDATA%\Temp"
set "TMP=%TEMP%"
set "PSModulePath=%ProgramFiles%\WindowsPowerShell\Modules;%SystemRoot%\System32\WindowsPowerShell\v1.0\Modules"
if not exist "%TEMP%" mkdir "%TEMP%"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0isolated-child.ps1" -Mode "%~1" -ExpectedSid "%~2" -InvocationId "%~4"
exit /b %ERRORLEVEL%
