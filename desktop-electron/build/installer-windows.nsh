; Keep legacy Tauri registration only for the production Astrion product.
; Process cleanup and rollback staging are restricted to the selected install.
!include "LogicLib.nsh"
!include "FileFunc.nsh"
; Test builds override only the old product identity, never production keys.
!ifndef ASTRION_LEGACY_PRODUCT
  !define ASTRION_LEGACY_PRODUCT "Astrion"
!endif
!ifndef ASTRION_LEGACY_BINARY
  !define ASTRION_LEGACY_BINARY "astrion-desktop.exe"
!endif
!ifndef BUILD_UNINSTALLER
  !if "${PRODUCT_NAME}" == "${ASTRION_LEGACY_PRODUCT}"
    Var AstrionLegacyDirectory
    Var AstrionLegacyRestart
  !endif
!else
  Var AstrionMoveSource
  Var AstrionMoveDestination
  Var AstrionRollbackDirectory

; NSIS Rename is atomic only on one volume. The stock uninstaller stages at
; $PLUGINSDIR (usually C:), which makes updates to E:/D: fail as "app running".
; Preserve the file-by-file busy guard and restoration using a sibling backup.
Function un.AstrionMoveTree
  Exch $R0
  Push $R1
  Push $R2
  Push $R3
  StrCpy $R3 0
  ; Bundled skills can exceed MAX_PATH under a user-selected install root.
  ; Use extended-length paths for enumeration, directory tests and moves.
  FindFirst $R1 $R2 "\\?\$AstrionMoveSource$R0\*.*"
  astrionMoveLoop:
    StrCmp $R2 "" astrionMoveDone
    StrCmp $R2 "." astrionMoveNext
    StrCmp $R2 ".." astrionMoveNext
    ${If} ${FileExists} "\\?\$AstrionMoveSource$R0\$R2\*.*"
      CreateDirectory "\\?\$AstrionMoveDestination$R0\$R2"
      Push "$R0\$R2"
      Call un.AstrionMoveTree
      Pop $R3
      StrCmp $R3 0 astrionMoveNext astrionMoveDone
    ${Else}
      ClearErrors
      Rename "\\?\$AstrionMoveSource$R0\$R2" "\\?\$AstrionMoveDestination$R0\$R2"
      IfErrors 0 astrionMoveNext
      StrCpy $R3 "$AstrionMoveSource$R0\$R2"
      Goto astrionMoveDone
    ${EndIf}
  astrionMoveNext:
    FindNext $R1 $R2
    Goto astrionMoveLoop
  astrionMoveDone:
    FindClose $R1
    StrCpy $R0 $R3
    Pop $R3
    Pop $R2
    Pop $R1
    Exch $R0
FunctionEnd
!endif

!macro customInit
  !if "${PRODUCT_NAME}" == "${ASTRION_LEGACY_PRODUCT}"
    StrCpy $AstrionLegacyDirectory ""
    StrCpy $AstrionLegacyRestart 0
    ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${ASTRION_LEGACY_PRODUCT}" "MainBinaryName"
    ${If} $0 == "${ASTRION_LEGACY_BINARY}"
      ReadRegStr $AstrionLegacyDirectory HKCU "Software\astrion\${ASTRION_LEGACY_PRODUCT}" ""
      ${If} $AstrionLegacyDirectory != ""
        ${GetParameters} $1
        ClearErrors
        ${GetOptions} $1 "/D=" $2
        ${If} ${Errors}
          StrCpy $INSTDIR $AstrionLegacyDirectory
        ${EndIf}
      ${EndIf}
    ${EndIf}
    ${GetParameters} $0
    ClearErrors
    ${GetOptions} $0 "/UPDATE" $1
    ${IfNot} ${Errors}
      SetSilent silent
      ; Tauri uses /R; electron-builder only recognizes --force-run.
      ClearErrors
      ${GetOptions} $0 "/R" $1
      ${IfNot} ${Errors}
        StrCpy $AstrionLegacyRestart 1
      ${EndIf}
    ${EndIf}
  !endif
!macroend

; The builder default kills every same-named process in other installations.
!macro customCheckAppRunning
  Push $0
  InitPluginsDir
  File /oname=$PLUGINSDIR\astrion-stop-backends.ps1 "${BUILD_RESOURCES_DIR}\stop-installed-backends.ps1"
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\astrion-stop-backends.ps1" -InstallDir "$INSTDIR" -ExecutableName "${APP_EXECUTABLE_FILENAME}"'
  Pop $0
  ${If} $0 != 0
    Pop $0
    SetErrorLevel 2
    Abort "Unable to release application files in the selected installation directory."
  ${EndIf}
  Pop $0
!macroend

!macro customRemoveFiles
  SetOutPath "$TEMP"
  ${If} ${isUpdated}
    ClearErrors
    GetTempFileName $AstrionRollbackDirectory "$INSTDIR\.."
    ${If} ${Errors}
      SetErrorLevel 2
      Abort "Unable to create rollback staging on the installation volume."
    ${EndIf}
    ; Extended-length paths do not resolve '..' components.
    GetFullPathName $AstrionRollbackDirectory "$AstrionRollbackDirectory"
    Delete "$AstrionRollbackDirectory"
    CreateDirectory "$AstrionRollbackDirectory"
    StrCpy $AstrionMoveSource "$INSTDIR"
    StrCpy $AstrionMoveDestination "$AstrionRollbackDirectory"
    Push ""
    Call un.AstrionMoveTree
    Pop $R0
    ${If} $R0 != 0
      StrCpy $AstrionMoveSource "$AstrionRollbackDirectory"
      StrCpy $AstrionMoveDestination "$INSTDIR"
      Push ""
      Call un.AstrionMoveTree
      Pop $R0
      ${If} $R0 == 0
        RMDir /r "\\?\$AstrionRollbackDirectory"
      ${EndIf}
      SetErrorLevel 2
      Abort "Application files are busy. Previous files were retained; rollback directory: $AstrionRollbackDirectory"
    ${EndIf}
    RMDir /r "\\?\$AstrionRollbackDirectory"
  ${EndIf}
  RMDir /r "\\?\$INSTDIR"
!macroend

!macro customInstall
  !if "${PRODUCT_NAME}" == "${ASTRION_LEGACY_PRODUCT}"
    ${If} $AstrionLegacyDirectory != ""
    ${AndIf} $AstrionLegacyDirectory == $INSTDIR
      ; Retire only the verified old ARP entry after successful installation.
      ; Old runtime and all user data remain available for rollback.
      DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${ASTRION_LEGACY_PRODUCT}"
      DeleteRegKey HKCU "Software\astrion\${ASTRION_LEGACY_PRODUCT}"
      Delete "$INSTDIR\uninstall.exe"
    ${EndIf}
    ${If} $AstrionLegacyRestart == 1
      ${StdUtils.ExecShellAsUser} $0 "$INSTDIR\${APP_EXECUTABLE_FILENAME}" "open" ""
    ${EndIf}
  !endif
!macroend
