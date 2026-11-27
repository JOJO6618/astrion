// Keep Windows release metadata independent from macOS package.json.
const release = require('./windows-release.json');
const buildId = require('./build/build-id.cjs');
module.exports = {
  appId: 'com.astrion.desktop',
  productName: 'Astrion',
  copyright: 'Copyright © 2026',
  directories: { output: '../output/windows-electron/build', buildResources: 'build' },
  files: ['src/**', '!src/windows/*.py', 'package.json'],
  extraMetadata: { version: release.version, astrionBuildId: buildId },
  extraResources: [
    { from: '../static/src/locales/zh-CN/updatePolicy.json', to: 'update-policy-locales/zh-CN.json' },
    { from: '../static/src/locales/en-US/updatePolicy.json', to: 'update-policy-locales/en-US.json' },
    { from: '.cache/windows-rust-backend', to: 'runtime/rust' },
    { from: '.cache/astrion-windows-helper.exe', to: 'astrion-windows-helper.exe' },
    { from: '../desktop/src-tauri/icons/icon.ico', to: 'icon.ico' },
    { from: '../desktop/browser-migration-extension', to: 'browser-migration-extension' }
  ],
  win: {
    icon: '../desktop/src-tauri/icons/icon.ico',
    executableName: 'astrion-desktop',
    target: [{ target: 'nsis', arch: ['x64'] }],
    artifactName: 'Astrion-${version}-windows-x64-setup.${ext}'
  },
  nsis: {
    // Pair these options: builder otherwise generates 7z but selects ZIP extraction.
    useZip: true,
    differentialPackage: false,
    oneClick: false,
    perMachine: false,
    allowElevation: false,
    allowToChangeInstallationDirectory: true,
    deleteAppDataOnUninstall: false,
    include: 'build/installer-windows.nsh',
    installerIcon: '../desktop/src-tauri/icons/icon.ico',
    uninstallDisplayName: 'Astrion',
    shortcutName: 'Astrion'
  },
  publish: { provider: 'generic', url: 'https://astrion.cyjai.com/downloads/', channel: 'latest' }
};
