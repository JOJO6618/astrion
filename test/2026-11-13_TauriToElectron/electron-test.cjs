const fs = require('node:fs');
const path = require('node:path');
const base = require('../../desktop-electron/electron-builder.win.cjs');
const cache = path.resolve(__dirname, '../../desktop-electron/.cache');
fs.mkdirSync(cache, { recursive: true });
const bootstrap = fs.readFileSync(path.resolve(__dirname, '../2026-11-13_WindowsElectron/installer-bootstrap.mjs'), 'utf8').replace("'19876'", "'19879'");
fs.writeFileSync(path.join(cache, 'cross-shell-bootstrap.mjs'), bootstrap);
module.exports = {
  ...base,
  appId: 'com.astrion.crossshell-test',
  productName: 'Astrion CrossShell Test',
  directories: { ...base.directories, output: path.resolve(__dirname, '../../output/windows-electron/cross-shell/electron') },
  files: [...base.files, '.cache/cross-shell-bootstrap.mjs'],
  extraMetadata: { name: 'astrion-crossshell-test', version: '0.10.1', main: '.cache/cross-shell-bootstrap.mjs', astrionMigrationTestRoot: path.resolve(__dirname, '../../output/windows-electron/cross-shell/run') },
  win: { ...base.win, executableName: 'astrion-crossshell-legacy', artifactName: 'Astrion-CrossShell-Electron-${version}.${ext}' },
  nsis: { ...base.nsis, include: path.join(__dirname, 'electron-include.nsh'), shortcutName: 'Astrion CrossShell Test', uninstallDisplayName: 'Astrion CrossShell Test', createDesktopShortcut: false, createStartMenuShortcut: false },
  publish: null
};
