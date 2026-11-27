@echo off
setlocal
cd /d "%~dp0"
if not exist node_modules call npm ci
if errorlevel 1 exit /b 1
if not exist static\dist\.vite\manifest.json call npm run build
if errorlevel 1 exit /b 1
node scripts\setup-host.mjs %*
exit /b %errorlevel%
