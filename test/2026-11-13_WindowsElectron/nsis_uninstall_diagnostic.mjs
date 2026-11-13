import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'output/windows-electron/nsis-diagnostic');
const installed = path.join(root, 'output/windows-electron/installer-test/installed-app');
fs.mkdirSync(output, { recursive: true });
const failure = path.join(output, 'failure.log');
if (fs.existsSync(failure)) fs.unlinkSync(failure);
// Generated diagnostic copy only: production source is unchanged.
const source = fs.readFileSync(path.join(root, 'desktop-electron/build/installer-windows.nsh'), 'utf8');
const needle = '      IfErrors 0 astrionMoveNext';
if (!source.includes(needle)) throw new Error('Diagnostic injection point changed');
fs.writeFileSync(path.join(output, 'hook.nsh'), source.replace(needle, `${needle}
      Push $0
      Push $1
      System::Call 'kernel32::GetLastError() i .r1'
      FileOpen $0 "${failure}" a
      FileWrite $0 "MOVE ERROR=$1 $AstrionMoveSource$R0\\$R2 -> $AstrionMoveDestination$R0\\$R2$\\r$\\n"
      FileClose $0
      Pop $1
      Pop $0`));
const script = `Unicode true
!define BUILD_UNINSTALLER
!define PRODUCT_NAME "Astrion NSIS Diagnostic"
!define isUpdated '1 = 1'
!include "${path.join(output, 'hook.nsh')}"
Name "Astrion NSIS Diagnostic"
OutFile "${path.join(output, 'bootstrap.exe')}"
InstallDir "${installed}"
RequestExecutionLevel user
SilentInstall silent
SilentUnInstall silent
Section
 WriteUninstaller "${path.join(output, 'uninstall.exe')}"
SectionEnd
Section "Uninstall"
 StrCmp $INSTDIR "${installed}" +3
 SetErrorLevel 9
 Quit
 !insertmacro customRemoveFiles
SectionEnd
`;
fs.writeFileSync(path.join(output, 'probe.nsi'), script);
const compiler = path.join(process.env.LOCALAPPDATA, 'electron-builder/Cache/nsis/nsis-3.0.4.1/makensis.exe');
for (const [exe, args] of [[compiler, [path.join(output, 'probe.nsi')]], [path.join(output, 'bootstrap.exe'), ['/S']], [path.join(output, 'uninstall.exe'), ['/S', `_?=${installed}`]]]) {
  const result = spawnSync(exe, args, { encoding: 'utf8', timeout: 120000, windowsHide: true });
  console.log(path.basename(exe), result.status, result.error?.message || '', result.stdout, result.stderr);
  if (result.status !== 0) break;
}
if (fs.existsSync(failure)) console.log('PRECISE MOVE FAILURE:', fs.readFileSync(failure, 'utf8'));
console.log('Test executable remains:', fs.existsSync(path.join(installed, 'astrion-electron-migration-test.exe')));
