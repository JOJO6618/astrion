// Separate product identity: this installer must never select the production
// Astrion registry keys, shortcuts, processes, install directory or update feed.
const path = require('node:path');
const fs = require('node:fs');
const base = require('../../desktop-electron/electron-builder.win.cjs');
const cache = path.resolve(__dirname, '../../desktop-electron/.cache');
fs.mkdirSync(cache, { recursive: true });
fs.copyFileSync(path.join(__dirname, 'installer-bootstrap.mjs'), path.join(cache, 'installer-bootstrap.mjs'));
const version = process.env.ASTRION_INSTALL_TEST_VERSION || '0.10.0';
const output = path.resolve(__dirname, '../../output/windows-electron/installer-test', version);
module.exports = {
  ...base,
  appId: 'com.astrion.electron-migration-test',
  productName: 'Astrion Electron Migration Test',
  directories: { ...base.directories, output },
  files: [...base.files, '.cache/installer-bootstrap.mjs'],
  extraMetadata: { name: 'astrion-electron-migration-test', version, main: '.cache/installer-bootstrap.mjs',
    astrionMigrationTestRoot: path.resolve(__dirname, '../../output/windows-electron/installer-test/run') },
  win: { ...base.win, executableName: 'astrion-electron-migration-test', artifactName: 'Astrion-Electron-Test-${version}.${ext}' },
  nsis: { ...base.nsis, useZip: true, differentialPackage: false, shortcutName: 'Astrion Electron Migration Test', uninstallDisplayName: 'Astrion Electron Migration Test', runAfterFinish: true, createDesktopShortcut: false, createStartMenuShortcut: false },
  publish: { provider: 'generic', url: 'http://127.0.0.1:19875/', channel: 'latest' }
};
