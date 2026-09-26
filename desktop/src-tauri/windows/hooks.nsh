; Astrion NSIS installer hooks (registered via bundle > windows > nsis > installerHooks)
;
; Why: when the shell process dies without its graceful exit path (updater's
; std::process::exit(0) after launching the installer, NSIS killing the main
; binary on manual reinstall, crashes), the embedded Python backend survives as
; an orphan and keeps file locks on runtime\, breaking install/update/uninstall
; with "Error opening file for writing" (reproduced via in-app update on 0.3.1).
;
; Kill leftover backends before install and uninstall:
;   1) >=0.3.2 backend runs as "astrion-backend.exe" (unique renamed copy of the
;      embedded interpreter) — safe to kill by name, current user only.
;   2) <=0.3.1 backend runs as the runtime's "python.exe" — killing "python.exe"
;      by name would nuke the user's own Python processes, so match by full
;      ExecutablePath via PowerShell. No match / no PowerShell -> no-op.
;
; Note on escaping: NSIS expands $$ to a literal $, so $${PSItem} reaches
; PowerShell as ${PSItem} (the $PSItem automatic variable, alias of $_).

!macro _AstrionKillBackendProcesses
  ; 1) New backend (>=0.3.2): unique process name, zero collateral damage
  !if "${INSTALLMODE}" == "currentUser"
    nsis_tauri_utils::KillProcessCurrentUser "astrion-backend.exe"
  !else
    nsis_tauri_utils::KillProcess "astrion-backend.exe"
  !endif
  Pop $R0

  ; 2) Legacy backend (<=0.3.1): exact ExecutablePath match under $INSTDIR only,
  ;    never touches system/user Python installs
  nsExec::ExecToStack `powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $${PSItem}.Name -eq 'python.exe' -and $${PSItem}.ExecutablePath -eq '$INSTDIR\runtime\python\python.exe' } | ForEach-Object { Stop-Process -Id $${PSItem}.ProcessId -Force -ErrorAction SilentlyContinue }"`
  Pop $R0
  Pop $R1

  ; Give the kernel a moment to release file locks
  Sleep 500
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro _AstrionKillBackendProcesses
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro _AstrionKillBackendProcesses
!macroend
