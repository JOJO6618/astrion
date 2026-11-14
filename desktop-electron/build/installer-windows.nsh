; Keep legacy Tauri registration only for the production Astrion product.
; Process cleanup and rollback staging are restricted to the selected install.
!include "LogicLib.nsh"
!include "FileFunc.nsh"

; electron-builder 的 multiUser 初始化在 HKCU\Software\<APP_GUID> 尚无
; InstallLocation 时会调用 SHELL32::SHGetKnownFolderPath。该调用在本机
; （Windows 11 26200 + nsis-resources 3.4.1 插件）稳定崩溃，异常码
; 0xc0000005、出错模块 System.dll，安装器在 .onInit 早期退出且什么都没装。
; preInit 比 initMultiUser 更早执行：先把安装位置写入该键，让模板走注册表
; 分支，值与模板自身默认值（$LocalAppData\Programs\${APP_FILENAME}）等价；
; 真实安装结束后 include/installer.nsh 会按最终 $INSTDIR 回写。
; 构建 uninstaller 时禁用：那时 .onInit 只在构建机跑，写入的会是临时目录。
!macro preInit
  !ifndef BUILD_UNINSTALLER
    ${If} $INSTDIR == ""
      StrCpy $INSTDIR "$LocalAppData\Programs\${APP_FILENAME}"
    ${EndIf}
    ReadRegStr $0 HKCU "Software\${APP_GUID}" "InstallLocation"
    ${If} $0 == ""
      WriteRegStr HKCU "Software\${APP_GUID}" "InstallLocation" "$INSTDIR"
    ${EndIf}
  !endif
!macroend
; Test builds override only the old product identity, never production keys.
!ifndef ASTRION_LEGACY_PRODUCT
  !define ASTRION_LEGACY_PRODUCT "Astrion"
!endif
!ifndef ASTRION_LEGACY_BINARY
  !define ASTRION_LEGACY_BINARY "astrion-desktop.exe"
!endif
!ifndef BUILD_UNINSTALLER
  ; Set by AstrionCheckRequired to the first payload file that did not land.
  Var AstrionMissing
  Var AstrionUnzipResult
  Var AstrionProgressDirectory
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

!macro AstrionProgressPhase PHASE
  !ifndef BUILD_UNINSTALLER
    ${If} $AstrionProgressDirectory != ""
      Push $9
      ClearErrors
      FileOpen $9 "$AstrionProgressDirectory\phase.txt" w
      ${IfNot} ${Errors}
        FileWrite $9 "${PHASE}"
        FileClose $9
      ${EndIf}
      Pop $9
    ${EndIf}
  !endif
!macroend

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
  ; Keep the assisted installer silent (its visible path changes $INSTDIR).
  ; A separate, per-install UI survives the old application exiting. The guard
  ; reuses this directory on its single retry instead of opening a second UI.
  !ifndef BUILD_UNINSTALLER
    StrCpy $AstrionProgressDirectory ""
    ${If} ${Silent}
      ReadEnvStr $AstrionProgressDirectory "ASTRION_INSTALL_PROGRESS_DIR"
      ${If} $AstrionProgressDirectory != ""
        ClearErrors
        FileOpen $9 "$AstrionProgressDirectory\install-directory.txt" r
        ${IfNot} ${Errors}
          FileReadUTF16LE $9 $0
          FileClose $9
          ${If} $0 != $INSTDIR
            StrCpy $AstrionProgressDirectory ""
          ${EndIf}
        ${Else}
          StrCpy $AstrionProgressDirectory ""
        ${EndIf}
      ${EndIf}
      ${If} $AstrionProgressDirectory == ""
        GetTempFileName $AstrionProgressDirectory "$TEMP"
        Delete "$AstrionProgressDirectory"
        CreateDirectory "$AstrionProgressDirectory"
        FileOpen $9 "$AstrionProgressDirectory\install-directory.txt" w
        FileWriteUTF16LE $9 "$INSTDIR"
        FileClose $9
        ${GetParameters} $0
        FileOpen $9 "$AstrionProgressDirectory\arguments.txt" w
        FileWriteUTF16LE $9 "$0"
        FileClose $9
        SetOutPath "$AstrionProgressDirectory"
        File "${BUILD_RESOURCES_DIR}\astrion-install-guard.ps1"
        File "${BUILD_RESOURCES_DIR}\astrion-install-progress.ps1"
        File "${BUILD_RESOURCES_DIR}\astrion-install-progress.strings.json"
        !insertmacro AstrionProgressPhase "preparing"
        System::Call 'kernel32::GetCurrentProcessId() i.r0'
        ; SW_HIDE applies when the process is created; -WindowStyle alone is
        ; parsed only after powershell.exe has already created its console.
        StrCpy $1 "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe"
        ${If} ${FileExists} "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
          StrCpy $1 "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
        ${EndIf}
        ExecShell "open" "$1" '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "$AstrionProgressDirectory\astrion-install-guard.ps1" -InstallDir "$INSTDIR" -InstallerPath "$EXEDIR\$EXEFILE" -InstallerPid $0 -WindowTitle "${PRODUCT_NAME}" -AppExe "${APP_EXECUTABLE_FILENAME}" -ProgressDirectory "$AstrionProgressDirectory"' SW_HIDE
      ${EndIf}
    ${EndIf}
  !endif
!macroend

; The builder default kills every same-named process in other installations.
!macro customCheckAppRunning
  !insertmacro AstrionProgressPhase "closing"
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
  !insertmacro AstrionProgressPhase "installing"
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

; The builder extracts the payload in a single nsisunz pass. If that pass is
; cut short (security software, an external kill, a full disk) it can end
; without an error while leaving the directory half-populated; the app then
; dies at startup with "Error loading V8 startup snapshot file". Verify the
; essential files and re-extract from the same in-place archive exactly once.
!macro AstrionCheckRequired
  StrCpy $AstrionMissing ""
  ${IfNot} ${FileExists} "$INSTDIR\${APP_EXECUTABLE_FILENAME}"
    StrCpy $AstrionMissing "${APP_EXECUTABLE_FILENAME}"
  ${EndIf}
  ${If} $AstrionMissing == ""
  ${AndIfNot} ${FileExists} "$INSTDIR\resources.pak"
    StrCpy $AstrionMissing "resources.pak"
  ${EndIf}
  ${If} $AstrionMissing == ""
  ${AndIfNot} ${FileExists} "$INSTDIR\snapshot_blob.bin"
    StrCpy $AstrionMissing "snapshot_blob.bin"
  ${EndIf}
  ${If} $AstrionMissing == ""
  ${AndIfNot} ${FileExists} "$INSTDIR\v8_context_snapshot.bin"
    StrCpy $AstrionMissing "v8_context_snapshot.bin"
  ${EndIf}
  ${If} $AstrionMissing == ""
  ${AndIfNot} ${FileExists} "$INSTDIR\resources\app.asar"
    StrCpy $AstrionMissing "resources\app.asar"
  ${EndIf}
  ; Only required when this package ships the bundled runtime, so diagnostic
  ; builds without extraResources are not rejected.
  ${If} $AstrionMissing == ""
  ${AndIf} ${FileExists} "$INSTDIR\resources\runtime"
  ${AndIfNot} ${FileExists} "$INSTDIR\resources\runtime\python\python.exe"
    StrCpy $AstrionMissing "resources\runtime\python\python.exe"
  ${EndIf}
!macroend

!macro customInstall
  !insertmacro AstrionProgressPhase "checking"
  ; A truncated extraction must fail loudly here instead of leaving an
  ; installation that cannot start.
  !insertmacro AstrionCheckRequired
  ${If} $AstrionMissing != ""
    DetailPrint "Astrion: payload check failed ($AstrionMissing); retrying extraction once."
    nsisunz::Unzip "$PLUGINSDIR\app-$packageArch.zip" "$INSTDIR"
    Pop $AstrionUnzipResult
    !insertmacro AstrionCheckRequired
    ${If} $AstrionMissing != ""
      ClearErrors
      FileOpen $9 "$INSTDIR\install-incomplete.log" w
      IfErrors astrionIncompleteNoLog
      FileWrite $9 "status=incomplete$\r$\n"
      FileWrite $9 "missing=$AstrionMissing$\r$\n"
      FileWrite $9 "unzip=$AstrionUnzipResult$\r$\n"
      FileClose $9
      astrionIncompleteNoLog:
      SetErrorLevel 3
      !insertmacro AstrionProgressPhase "incomplete"
      DetailPrint "Astrion: still missing $AstrionMissing after one retry; aborting."
      ${IfNot} ${Silent}
        MessageBox MB_ICONSTOP|MB_OK "安装未完成：缺少 $AstrionMissing。请重新运行安装包（建议暂时关闭安全软件）。"
      ${EndIf}
      Abort
    ${EndIf}
  ${EndIf}
  !if "${PRODUCT_NAME}" == "${ASTRION_LEGACY_PRODUCT}"
    ${If} $AstrionLegacyDirectory != ""
    ${AndIf} $AstrionLegacyDirectory == $INSTDIR
      ; Retire only the verified old ARP entry after successful installation.
      ; Old runtime and all user data remain available for rollback.
      DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${ASTRION_LEGACY_PRODUCT}"
      DeleteRegKey HKCU "Software\astrion\${ASTRION_LEGACY_PRODUCT}"
      Delete "$INSTDIR\uninstall.exe"
    ${EndIf}
  !endif
  ; Only this fresh per-run marker confirms extraction and registration ran.
  ${If} $AstrionProgressDirectory != ""
    FileOpen $9 "$AstrionProgressDirectory\installed.txt" w
    FileWrite $9 "${VERSION}"
    FileClose $9
  ${EndIf}
  !insertmacro AstrionProgressPhase "starting"
  ; Do not let a retry session leak into the new app and its next update.
  System::Call 'kernel32::SetEnvironmentVariableW(w "ASTRION_INSTALL_PROGRESS_DIR", p 0)'
  !if "${PRODUCT_NAME}" == "${ASTRION_LEGACY_PRODUCT}"
    ${If} $AstrionLegacyRestart == 1
      ${StdUtils.ExecShellAsUser} $0 "$INSTDIR\${APP_EXECUTABLE_FILENAME}" "open" ""
    ${EndIf}
  !endif
!macroend
