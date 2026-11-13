// Small isolated product exercising the production NSIS hooks without Electron.
const path = require('node:path');
const base = require('../../desktop-electron/electron-builder.win.cjs');
const version = process.env.ASTRION_NSIS_PROBE_VERSION || '0.10.0';
module.exports = {
  ...base,
  appId: 'com.astrion.nsis-probe',
  productName: 'Astrion NSIS Probe',
  directories: { ...base.directories, output: path.resolve(__dirname, '../../output/windows-electron/nsis-probe', version) },
  extraMetadata: { name: 'astrion-nsis-probe', version, main: 'index.js' },
  extraResources: [],
  win: { ...base.win, executableName: 'astrion-nsis-probe', artifactName: 'Astrion-NSIS-Probe-${version}.${ext}' },
  nsis: { ...base.nsis, runAfterFinish: false, createDesktopShortcut: false, createStartMenuShortcut: false, shortcutName: 'Astrion NSIS Probe', uninstallDisplayName: 'Astrion NSIS Probe' },
  publish: null
};
