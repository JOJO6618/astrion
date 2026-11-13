@echo off
setlocal
set "ASTRION_ELECTRON_USER_DATA=%~dp0validation-profile\electron"
set "ASTRION_DESKTOP_DATA_ROOT=%~dp0validation-profile\data"
set "ASTRION_DESKTOP_SETTINGS_FILE=%~dp0validation-profile\rundata-settings.json"
set "ASTRION_QUICK_SETTINGS_FILE=%~dp0validation-profile\quick-settings.json"
set "ASTRION_QUICK_DISCOVERY_FILE=%~dp0validation-profile\gateway.json"
set "ASTRION_DESKTOP_INITIAL_WORKSPACE=%~dp0validation-profile\workspace"
if not exist "%ASTRION_DESKTOP_INITIAL_WORKSPACE%" mkdir "%ASTRION_DESKTOP_INITIAL_WORKSPACE%"
start "" "%~dp0App\astrion-desktop.exe"
