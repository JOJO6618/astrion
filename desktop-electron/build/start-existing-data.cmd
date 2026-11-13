@echo off
setlocal
set "ASTRION_ELECTRON_USER_DATA="
set "ASTRION_DESKTOP_DATA_ROOT="
set "ASTRION_DESKTOP_SETTINGS_FILE="
set "ASTRION_QUICK_SETTINGS_FILE="
set "ASTRION_QUICK_DISCOVERY_FILE="
set "ASTRION_DESKTOP_INITIAL_WORKSPACE="
echo This launch uses your existing Astrion desktop runtime data.
echo Close the old Astrion desktop application before continuing.
pause
start "" "%~dp0App\astrion-desktop.exe"
