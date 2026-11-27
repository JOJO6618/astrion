@echo off
setlocal
cd /d "%~dp0"
node scripts\host.mjs %*
exit /b %errorlevel%
