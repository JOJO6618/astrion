const path = require('node:path');
const base = require('../../desktop-electron/electron-builder.win.cjs');

// Repackage the existing complete 0.10.0 application with the new NSIS UI.
// Keep the previous published installer and its validation evidence intact.
module.exports = {
  ...base,
  directories: {
    ...base.directories,
    output: path.resolve(__dirname, '../../output/windows-update-progress-20261114/build'),
  },
};
